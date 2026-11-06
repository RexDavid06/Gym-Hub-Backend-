import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from memberships.models import MembershipPayment, MembershipSubscription
from notifications.models import Notification
from notifications.providers import (
    PermanentProviderError,
    ProviderError,
    TransientProviderError,
    get_provider,
)
from notifications.services import (
    delivery_skip_reason,
    queue_membership_reminders,
    queue_payment_receipt,
)

logger = logging.getLogger(__name__)

STALE_SENDING_AFTER = timedelta(minutes=15)
MAX_ATTEMPTS = 3
RETRY_DELAYS = {1: 60, 2: 300}


@shared_task
def celery_ping():
    return "pong"


@shared_task
def schedule_membership_reminders():
    today = timezone.localdate()
    subscriptions = (
        MembershipSubscription.objects.filter(
            status__in=[
                MembershipSubscription.Status.ACTIVE,
                MembershipSubscription.Status.EXPIRED,
            ]
        )
        .select_related("gym", "member", "plan")
        .order_by("pk")
    )
    created = 0
    for subscription in subscriptions.iterator():
        created += len(queue_membership_reminders(subscription, today))
    return created


@shared_task
def send_payment_receipt(payment_id, gym_id=None):
    payment = (
        MembershipPayment.objects.filter(pk=payment_id)
        .select_related("gym", "member", "subscription__plan")
        .first()
    )
    if payment is None:
        return 0
    if gym_id is not None and payment.gym_id != gym_id:
        return 0
    return len(queue_payment_receipt(payment))


def _mark_failed(notification_id, message, *, provider_name="", retry=False):
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(pk=notification_id)
        notification.status = Notification.Status.FAILED
        notification.last_error = message[:1000]
        if provider_name:
            notification.provider = provider_name
        notification.save(update_fields=["status", "last_error", "provider", "updated_at"])
        attempts = notification.attempts
    if retry:
        deliver_notification.apply_async(
            args=[notification_id], countdown=RETRY_DELAYS.get(attempts, 300)
        )


def _mark_sent(notification_id, provider_name, provider_message_id):
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(pk=notification_id)
        notification.status = Notification.Status.SENT
        notification.provider = provider_name
        notification.provider_message_id = provider_message_id or ""
        notification.last_error = ""
        notification.sent_at = timezone.now()
        notification.save(
            update_fields=[
                "status",
                "provider",
                "provider_message_id",
                "last_error",
                "sent_at",
                "updated_at",
            ]
        )


@shared_task
def deliver_notification(notification_id):
    now = timezone.now()
    with transaction.atomic():
        notification = (
            Notification.objects.select_for_update()
            .select_related("gym", "recipient")
            .filter(pk=notification_id)
            .first()
        )
        if notification is None:
            return
        if notification.status in {Notification.Status.SENT, Notification.Status.SKIPPED}:
            return
        if (
            notification.status == Notification.Status.SENDING
            and notification.updated_at > now - STALE_SENDING_AFTER
        ):
            return

        skip_reason = delivery_skip_reason(notification)
        if skip_reason:
            notification.status = Notification.Status.SKIPPED
            notification.skip_reason = skip_reason
            notification.last_error = ""
            notification.save(update_fields=["status", "skip_reason", "last_error", "updated_at"])
            return

        try:
            provider = get_provider(notification.channel)
        except PermanentProviderError as error:
            notification.status = Notification.Status.FAILED
            notification.last_error = str(error)[:1000]
            notification.save(update_fields=["status", "last_error", "updated_at"])
            return

        notification.status = Notification.Status.SENDING
        notification.attempts += 1
        notification.provider = provider.name
        notification.last_error = ""
        notification.save(
            update_fields=["status", "attempts", "provider", "last_error", "updated_at"]
        )

        channel = notification.channel
        recipient_phone = notification.recipient_phone
        body = notification.body
        template_name = notification.template_name
        parameters = (notification.payload or {}).get("template_parameters", [])
        dedupe_key = notification.dedupe_key
        attempts = notification.attempts

    try:
        if channel == Notification.Channel.SMS:
            provider_message_id = provider.send(
                to=recipient_phone, message=body, idempotency_key=dedupe_key
            )
        else:
            if not template_name:
                raise PermanentProviderError("WhatsApp template is not configured.")
            provider_message_id = provider.send(
                to=recipient_phone,
                template_name=template_name,
                language=settings.WHATSAPP_TEMPLATE_LANGUAGE,
                parameters=parameters,
                idempotency_key=dedupe_key,
            )
    except TransientProviderError as error:
        _mark_failed(
            notification_id,
            str(error),
            provider_name=provider.name,
            retry=attempts < MAX_ATTEMPTS,
        )
        return
    except PermanentProviderError as error:
        _mark_failed(notification_id, str(error), provider_name=provider.name)
        return
    except ProviderError as error:
        _mark_failed(notification_id, str(error), provider_name=provider.name)
        return
    except Exception:
        logger.warning("Unexpected notification delivery failure for %s", notification_id)
        _mark_failed(
            notification_id,
            "Unexpected delivery error.",
            provider_name=provider.name,
            retry=attempts < MAX_ATTEMPTS,
        )
        return

    _mark_sent(notification_id, provider.name, provider_message_id)
