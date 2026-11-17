"""Celery tasks for Phase 10 billing.

All heavy work (billing runs, dunning, reconciliation) runs here, never inside a
request. Every task is idempotent so retries and reruns are safe.
"""

import logging
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import User
from billing import services
from billing.models import (
    BillingPayment,
    DunningAttempt,
    GymBillingSubscription,
    Invoice,
    WebhookEvent,
)
from billing.paystack import (
    PaystackError,
    provider_enabled,
    verify_transaction,
)
from config.celery import app as celery_app  # noqa: F401  (configures the default app)
from notifications.providers import (
    PermanentProviderError,
    ProviderError,
    get_sms_provider,
    sms_available,
)

logger = logging.getLogger(__name__)


@shared_task
def generate_billing_invoices_batch(subscription_ids):
    """Issue the next invoice for one bounded batch of subscriptions."""
    return services.generate_due_invoices(subscription_ids=subscription_ids)


@shared_task
def generate_billing_invoices():
    """Fan out the billing run into bounded batches (idempotent per period)."""
    today = timezone.localdate()
    due_ids = list(
        GymBillingSubscription.objects.filter(current_period_end__lt=today)
        .exclude(status=GymBillingSubscription.Status.CANCELLED)
        .order_by("pk")
        .values_list("id", flat=True)
    )
    batch_size = max(getattr(settings, "BILLING_RUN_BATCH_SIZE", 200), 1)
    created = 0
    eager = getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    for start in range(0, len(due_ids), batch_size):
        batch = due_ids[start : start + batch_size]
        result = generate_billing_invoices_batch.delay(batch)
        if eager:
            created += result.get() or 0
    return created


def owner_for(gym):
    return (
        User.objects.filter(gym=gym, role=User.Role.OWNER, is_active=True)
        .order_by("date_joined", "id")
        .first()
    )


def dunning_message(invoice):
    amount = f"{invoice.currency} {invoice.amount}"
    return (
        f"Gym Hub: invoice {invoice.number} for {invoice.plan_name} "
        f"({amount}) is due {invoice.due_at}. Please settle to keep your account active."
    )


def _send_dunning(invoice, attempt):
    owner = owner_for(invoice.gym)
    if owner is None or not owner.phone:
        attempt.status = DunningAttempt.Status.SKIPPED
        attempt.error = "No owner phone number on file."
        attempt.save(update_fields=["status", "error"])
        return attempt
    if not sms_available():
        attempt.status = DunningAttempt.Status.SKIPPED
        attempt.error = "SMS is not available in this environment."
        attempt.save(update_fields=["status", "error"])
        return attempt
    try:
        provider = get_sms_provider()
        provider.send(
            to=owner.phone,
            message=dunning_message(invoice),
            idempotency_key=f"billing-dunning-{invoice.pk}-{attempt.offset_days}",
        )
    except (PermanentProviderError, ProviderError) as error:
        attempt.status = DunningAttempt.Status.FAILED
        attempt.error = str(error)[:1000]
        attempt.save(update_fields=["status", "error"])
        return attempt
    except Exception:  # pragma: no cover - defensive
        attempt.status = DunningAttempt.Status.FAILED
        attempt.error = "Unexpected dunning delivery error."
        attempt.save(update_fields=["status", "error"])
        return attempt
    attempt.status = DunningAttempt.Status.SENT
    attempt.sent_at = timezone.now()
    attempt.error = ""
    attempt.save(update_fields=["status", "sent_at", "error"])
    return attempt


@shared_task
def run_billing_dunning():
    """Mark overdue invoices, send due reminders, and optionally auto-suspend."""
    today = timezone.localdate()
    services.mark_overdue(today)
    sent = 0
    if getattr(settings, "BILLING_DUNNING_ENABLED", False):
        for invoice in (
            services.dunning_candidates(today).select_related("gym", "plan").order_by("pk")
        ):
            for offset in services.due_dunning_offsets(invoice, today):
                attempt, created = services.create_dunning_attempt(invoice, offset)
                if not created:
                    continue
                _send_dunning(invoice, attempt)
                sent += 1
    if getattr(settings, "BILLING_AUTO_SUSPEND_ENABLED", False):
        for subscription in GymBillingSubscription.objects.select_related("gym").order_by("pk"):
            services.maybe_auto_suspend(subscription, today)
    return sent


def _record_paystack_charge(data):
    """Record one signed Paystack charge against the invoice it names."""
    metadata = data.get("metadata") or {}
    invoice = None
    invoice_id = metadata.get("invoice_id")
    if invoice_id:
        invoice = Invoice.objects.filter(pk=invoice_id).first()
    reference = str(data.get("reference") or "")
    if invoice is None and reference:
        invoice = Invoice.objects.filter(reference=reference).first()
    if invoice is None:
        return None
    if BillingPayment.objects.filter(
        provider=BillingPayment.Provider.PAYSTACK, provider_reference=reference
    ).exists():
        return None
    amount = data.get("amount")
    if not amount:
        return None
    return services.record_payment(
        invoice=invoice,
        amount=Decimal(amount) / 100,
        method=BillingPayment.Method.CARD,
        provider=BillingPayment.Provider.PAYSTACK,
        provider_reference=reference,
    )


@shared_task
def process_paystack_webhook(event_id):
    """Record a verified webhook event in the background, exactly once."""
    event = WebhookEvent.objects.filter(pk=event_id).first()
    if event is None or event.processed_at is not None:
        return 0
    try:
        if event.event_type == "charge.success":
            _record_paystack_charge(event.payload.get("data") or {})
    except ValidationError as error:
        logger.warning("Paystack webhook %s could not be applied: %s", event.event_id, error)
    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at"])
    return 1


@shared_task
def reconcile_paystack_payments():
    """Verify open invoices with a checkout reference and record missed payments."""
    if not provider_enabled():
        return 0
    recorded = 0
    invoices = (
        Invoice.objects.filter(status__in=[Invoice.Status.OPEN, Invoice.Status.OVERDUE])
        .exclude(reference="")
        .order_by("pk")
    )
    for invoice in invoices:
        try:
            data = verify_transaction(invoice.reference)
        except (PaystackError, ProviderError):
            continue
        if str(data.get("status", "")).lower() != "success":
            continue
        if _record_paystack_charge(
            {
                "reference": str(data.get("reference") or invoice.reference),
                "amount": data.get("amount"),
                "metadata": {"invoice_id": invoice.pk},
            }
        ):
            recorded += 1
    return recorded
