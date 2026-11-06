from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone

from memberships.models import MembershipPayment, MembershipSubscription
from notifications.models import Notification, NotificationPreference, NotificationSetting

REMINDER_SCHEDULE = (
    ("3m", relativedelta(months=3), Notification.Event.MEMBERSHIP_EXPIRING),
    ("1m", relativedelta(months=1), Notification.Event.MEMBERSHIP_EXPIRING),
    ("7d", relativedelta(days=7), Notification.Event.MEMBERSHIP_EXPIRING),
    ("3d", relativedelta(days=3), Notification.Event.MEMBERSHIP_EXPIRING),
    ("due", relativedelta(), Notification.Event.MEMBERSHIP_EXPIRING),
    ("7d_after", relativedelta(days=-7), Notification.Event.MEMBERSHIP_EXPIRED),
)


def format_date(value):
    return value.strftime("%d %b %Y")


def format_amount(value):
    return f"NGN {value:,.2f}"


def member_name(user):
    return (user.name or "").strip() or user.phone


def schedule_targets(end_date):
    for schedule_key, offset, event in REMINDER_SCHEDULE:
        yield schedule_key, end_date - offset, event


def channel_enabled(setting, event, channel):
    if event == Notification.Event.PAYMENT_RECEIPT:
        if channel == Notification.Channel.SMS:
            return setting.sms_receipts_enabled
        return setting.whatsapp_receipts_enabled
    if channel == Notification.Channel.SMS:
        return setting.sms_reminders_enabled
    return setting.whatsapp_reminders_enabled


def whatsapp_opted_in(gym_id, user_id):
    return NotificationPreference.objects.filter(
        gym_id=gym_id, user_id=user_id, whatsapp_opt_in=True
    ).exists()


def channels_for_event(gym, event, member):
    setting = NotificationSetting.objects.filter(gym_id=gym.pk).first()
    if setting is None:
        return []
    channels = []
    if channel_enabled(setting, event, Notification.Channel.SMS):
        channels.append(Notification.Channel.SMS)
    if channel_enabled(setting, event, Notification.Channel.WHATSAPP) and whatsapp_opted_in(
        gym.pk, member.pk
    ):
        channels.append(Notification.Channel.WHATSAPP)
    return channels


def whatsapp_template_name(event):
    if event == Notification.Event.PAYMENT_RECEIPT:
        return settings.WHATSAPP_TEMPLATE_PAYMENT_RECEIPT
    if event == Notification.Event.MEMBERSHIP_EXPIRED:
        return settings.WHATSAPP_TEMPLATE_MEMBERSHIP_EXPIRED
    return settings.WHATSAPP_TEMPLATE_MEMBERSHIP_REMINDER


def create_notification(
    *,
    gym,
    recipient,
    event,
    schedule_key,
    channel,
    dedupe_key,
    body,
    payload,
    template_name="",
):
    notification, created = Notification.objects.get_or_create(
        gym=gym,
        dedupe_key=dedupe_key,
        defaults={
            "recipient": recipient,
            "recipient_phone": recipient.phone,
            "event": event,
            "schedule_key": schedule_key,
            "channel": channel,
            "body": body,
            "payload": payload,
            "template_name": template_name,
            "scheduled_at": timezone.now(),
        },
    )
    if created:
        from notifications.tasks import deliver_notification

        deliver_notification.delay(notification.pk)
    return notification, created


def queue_payment_receipt(payment):
    if payment.is_voided:
        return []
    gym = payment.gym
    member = payment.member
    if not gym.is_active or not member.is_active or not member.phone:
        return []

    event = Notification.Event.PAYMENT_RECEIPT
    channels = channels_for_event(gym, event, member)
    if not channels:
        return []

    subscription = payment.subscription
    plan_name = subscription.plan.name if subscription else "membership"
    end_date = subscription.end_date if subscription else payment.paid_at
    body = (
        f"Hi {member_name(member)}, {gym.name} received {format_amount(payment.amount)} "
        f"for {plan_name}. Receipt {payment.receipt_number}. "
        f"Valid until {format_date(end_date)}."
    )
    template_parameters = [
        member_name(member),
        gym.name,
        format_amount(payment.amount),
        plan_name,
        payment.receipt_number,
        format_date(end_date),
    ]
    payload = {
        "payment_id": payment.pk,
        "member_id": member.pk,
        "subscription_id": subscription.pk if subscription else None,
        "plan_id": subscription.plan_id if subscription else None,
        "end_date": end_date.isoformat(),
        "template_parameters": template_parameters,
    }

    notifications = []
    for channel in channels:
        notification, created = create_notification(
            gym=gym,
            recipient=member,
            event=event,
            schedule_key=Notification.ScheduleKey.RECEIPT,
            channel=channel,
            dedupe_key=f"payment_receipt:{payment.pk}:{channel}",
            body=body,
            payload=payload,
            template_name=(
                whatsapp_template_name(event) if channel == Notification.Channel.WHATSAPP else ""
            ),
        )
        if created:
            notifications.append(notification)
    return notifications


def queue_membership_reminders(subscription, today=None):
    today = today or timezone.localdate()
    gym = subscription.gym
    member = subscription.member
    if subscription.status == MembershipSubscription.Status.CANCELLED:
        return []
    if not gym.is_active or not member.is_active or not member.phone:
        return []

    notifications = []
    for schedule_key, target, event in schedule_targets(subscription.end_date):
        if target != today or target < subscription.start_date:
            continue
        if (
            schedule_key != "7d_after"
            and subscription.status != MembershipSubscription.Status.ACTIVE
        ):
            continue
        channels = channels_for_event(gym, event, member)
        if not channels:
            continue

        plan_name = subscription.plan.name
        if event == Notification.Event.MEMBERSHIP_EXPIRED:
            body = (
                f"Hi {member_name(member)}, your {plan_name} membership at {gym.name} "
                f"expired on {format_date(subscription.end_date)}. Please renew at the gym."
            )
        else:
            body = (
                f"Hi {member_name(member)}, your {plan_name} membership at {gym.name} "
                f"expires on {format_date(subscription.end_date)}. Please renew at the gym."
            )
        template_parameters = [
            member_name(member),
            plan_name,
            gym.name,
            format_date(subscription.end_date),
        ]
        payload = {
            "subscription_id": subscription.pk,
            "member_id": member.pk,
            "plan_id": subscription.plan_id,
            "end_date": subscription.end_date.isoformat(),
            "template_parameters": template_parameters,
        }

        for channel in channels:
            notification, created = create_notification(
                gym=gym,
                recipient=member,
                event=event,
                schedule_key=schedule_key,
                channel=channel,
                dedupe_key=(
                    f"{event}:{subscription.pk}:{subscription.end_date}:"
                    f"{schedule_key}:{channel}"
                ),
                body=body,
                payload=payload,
                template_name=(
                    whatsapp_template_name(event)
                    if channel == Notification.Channel.WHATSAPP
                    else ""
                ),
            )
            if created:
                notifications.append(notification)
    return notifications


def delivery_skip_reason(notification):
    gym = notification.gym
    recipient = notification.recipient
    if not gym.is_active:
        return "gym_inactive"
    if recipient is None:
        return "recipient_missing"
    if not recipient.is_active:
        return "member_inactive"
    if not recipient.phone:
        return "missing_phone"

    setting = NotificationSetting.objects.filter(gym_id=notification.gym_id).first()
    if setting is None or not channel_enabled(setting, notification.event, notification.channel):
        return "channel_disabled"
    if notification.channel == Notification.Channel.WHATSAPP and not whatsapp_opted_in(
        notification.gym_id, recipient.pk
    ):
        return "whatsapp_not_opted_in"

    payload = notification.payload or {}
    if notification.event == Notification.Event.PAYMENT_RECEIPT:
        payment = (
            MembershipPayment.objects.filter(
                pk=payload.get("payment_id"), gym_id=notification.gym_id
            )
            .select_related("member")
            .first()
        )
        if payment is None:
            return "payment_missing"
        if payment.is_voided:
            return "payment_voided"
        if payment.member_id != recipient.pk:
            return "recipient_mismatch"
        return ""

    subscription = (
        MembershipSubscription.objects.filter(
            pk=payload.get("subscription_id"), gym_id=notification.gym_id
        )
        .select_related("member")
        .first()
    )
    if subscription is None:
        return "subscription_missing"
    if subscription.status == MembershipSubscription.Status.CANCELLED:
        return "subscription_cancelled"
    if subscription.member_id != recipient.pk:
        return "recipient_mismatch"
    if payload.get("end_date") and payload["end_date"] != subscription.end_date.isoformat():
        return "subscription_changed"
    if (
        notification.event == Notification.Event.MEMBERSHIP_EXPIRING
        and subscription.end_date < timezone.localdate()
    ):
        return "subscription_expired"
    return ""
