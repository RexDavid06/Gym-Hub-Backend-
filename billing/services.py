"""Billing domain services: invoice generation, payment recording, dunning.

Keeping this logic out of the views lets the API and the Celery tasks share the
same, tested code paths.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import (
    BillingPayment,
    DunningAttempt,
    GymBillingSubscription,
    Invoice,
    period_end_for,
)

ZERO = Decimal("0.00")


def _duration_months(subscription):
    return subscription.price.duration_months or 1


def _issue_invoice(subscription, period_start, period_end, *, created_by=None, issued_at=None):
    """Create an invoice for one period, idempotent per (subscription, period)."""
    issued_at = issued_at or timezone.localdate()
    due_days = getattr(settings, "BILLING_DUE_DAYS", 7)
    with transaction.atomic():
        existing = (
            Invoice.objects.select_for_update()
            .filter(subscription=subscription, period_start=period_start)
            .first()
        )
        if existing is not None:
            return existing, False
        invoice = Invoice.objects.create(
            gym=subscription.gym,
            subscription=subscription,
            plan=subscription.plan,
            plan_name=subscription.plan.name,
            interval=subscription.interval,
            issued_at=issued_at,
            due_at=issued_at + timedelta(days=due_days),
            period_start=period_start,
            period_end=period_end,
            description=f"{subscription.plan.name} ({subscription.interval})",
            amount=subscription.price.amount,
            currency=subscription.price.currency,
            created_by=created_by,
        )
        return invoice, True


def issue_invoice_for_subscription(subscription, *, created_by=None, issued_at=None):
    """Create (idempotently) the invoice for a subscription's current period."""
    invoice, _ = _issue_invoice(
        subscription,
        subscription.current_period_start,
        subscription.current_period_end,
        created_by=created_by,
        issued_at=issued_at,
    )
    return invoice


def create_subscription(*, gym, plan, price, status=None, created_by=None, start=None):
    """Create a gym's billing subscription and its first invoice."""
    start = start or timezone.localdate()
    subscription = GymBillingSubscription.objects.create(
        gym=gym,
        plan=plan,
        price=price,
        interval=price.interval,
        status=status or GymBillingSubscription.Status.ACTIVE,
        current_period_start=start,
        current_period_end=period_end_for(start, _duration_months_from_price(price)),
    )
    issue_invoice_for_subscription(subscription, created_by=created_by)
    return subscription


def _duration_months_from_price(price):
    return price.duration_months or 1


def _advance_subscription_after_payment(subscription):
    """Advance a paid subscription and issue the next period's invoice in advance.

    A subscription marked ``cancel_at_period_end`` becomes ``cancelled`` and is
    not billed again.
    """
    if subscription.cancel_at_period_end:
        subscription.status = GymBillingSubscription.Status.CANCELLED
        subscription.save(update_fields=["status", "updated_at"])
        return None
    subscription.status = GymBillingSubscription.Status.ACTIVE
    subscription.advance_period()
    return issue_invoice_for_subscription(subscription)


def record_payment(
    *,
    invoice,
    amount,
    method=BillingPayment.Method.TRANSFER,
    provider=BillingPayment.Provider.MANUAL,
    provider_reference="",
    reference="",
    paid_at=None,
    recorded_by=None,
):
    """Record a payment against an invoice and roll the subscription forward."""
    amount = Decimal(amount)
    if amount <= ZERO:
        raise ValidationError({"amount": "The amount must be greater than zero."})
    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.status == Invoice.Status.VOID:
            raise ValidationError({"detail": "A voided invoice cannot receive payments."})
        payment = BillingPayment.objects.create(
            gym=locked.gym,
            invoice=locked,
            plan=locked.plan,
            amount=amount,
            method=method,
            provider=provider,
            provider_reference=provider_reference,
            reference=reference,
            paid_at=paid_at or timezone.localdate(),
            recorded_by=recorded_by,
        )
        locked.apply_payment(amount)
        if locked.status == Invoice.Status.PAID and locked.subscription_id:
            subscription = GymBillingSubscription.objects.select_for_update().get(
                pk=locked.subscription_id
            )
            if locked.period_start == subscription.current_period_start:
                _advance_subscription_after_payment(subscription)
            elif subscription.status == GymBillingSubscription.Status.PAST_DUE:
                subscription.status = GymBillingSubscription.Status.ACTIVE
                subscription.save(update_fields=["status", "updated_at"])
        return payment


def generate_due_invoices(today=None, *, subscription_ids=None):
    """Issue the next invoice for every subscription whose period has lapsed.

    Idempotent per (subscription, period_start); a rerun never double-bills and a
    crash between issuing and advancing is recoverable. Returns the number of
    invoices actually created. ``subscription_ids`` lets the Celery billing run
    process a bounded batch instead of every gym at once.
    """
    today = today or timezone.localdate()
    created = 0
    subscriptions = GymBillingSubscription.objects.filter(current_period_end__lt=today).exclude(
        status=GymBillingSubscription.Status.CANCELLED
    )
    if subscription_ids is not None:
        subscriptions = subscriptions.filter(pk__in=subscription_ids)
    subscriptions = subscriptions.select_related("gym", "plan", "price").order_by("pk")
    for subscription in subscriptions.iterator():
        if subscription.cancel_at_period_end:
            subscription.status = GymBillingSubscription.Status.CANCELLED
            subscription.save(update_fields=["status", "updated_at"])
            continue
        next_start = subscription.current_period_end + timedelta(days=1)
        next_end = period_end_for(next_start, _duration_months(subscription))
        _, was_created = _issue_invoice(subscription, next_start, next_end, issued_at=today)
        subscription.current_period_start = next_start
        subscription.current_period_end = next_end
        subscription.save(
            update_fields=["current_period_start", "current_period_end", "updated_at"]
        )
        created += 1 if was_created else 0
    return created


def mark_overdue(today=None):
    """Move open invoices past their due date to ``overdue``."""
    today = today or timezone.localdate()
    return Invoice.objects.filter(status=Invoice.Status.OPEN, due_at__lt=today).update(
        status=Invoice.Status.OVERDUE
    )


def overdue_invoices(today=None):
    today = today or timezone.localdate()
    return Invoice.objects.filter(
        status__in=[Invoice.Status.OPEN, Invoice.Status.OVERDUE], due_at__lt=today
    )


def _dunning_offsets():
    return getattr(settings, "BILLING_DUNNING_OFFSETS", [-3, 0, 3, 7, 14])


def due_dunning_offsets(invoice, today=None):
    """Which configured dunning offsets apply to an invoice today.

    An offset is ``(today - due_at).days``: negative values are reminders before
    the due date (``-3`` means three days early), zero is the due date, and
    positive values are days overdue.
    """
    today = today or timezone.localdate()
    days_since_due = (today - invoice.due_at).days
    return [offset for offset in _dunning_offsets() if offset == days_since_due]


def dunning_candidates(today=None):
    """Open/overdue invoices whose due date falls inside the configured window."""
    today = today or timezone.localdate()
    offsets = _dunning_offsets()
    if not offsets:
        return Invoice.objects.none()
    window_start = today - timedelta(days=max(offsets))
    window_end = today - timedelta(days=min(offsets))
    return Invoice.objects.filter(
        status__in=[Invoice.Status.OPEN, Invoice.Status.OVERDUE],
        due_at__range=(window_start, window_end),
    )


def create_dunning_attempt(invoice, offset_days, *, channel=DunningAttempt.Channel.SMS):
    attempt, created = DunningAttempt.objects.get_or_create(
        invoice=invoice, offset_days=offset_days, channel=channel
    )
    return attempt, created


def maybe_auto_suspend(subscription, today=None):
    """Suspend a gym that is past the grace period with an unpaid invoice."""
    if not getattr(settings, "BILLING_AUTO_SUSPEND_ENABLED", False):
        return None
    today = today or timezone.localdate()
    grace = getattr(settings, "BILLING_GRACE_PERIOD_DAYS", 14)
    oldest_overdue = overdue_invoices(today).filter(gym=subscription.gym).order_by("due_at").first()
    if oldest_overdue is None:
        return None
    if (today - oldest_overdue.due_at).days < grace:
        return None
    gym = subscription.gym
    if not gym.is_active:
        return None
    gym.suspend(by=None, reason=f"Automated: unpaid invoice {oldest_overdue.number}.")
    subscription.status = GymBillingSubscription.Status.PAST_DUE
    subscription.save(update_fields=["status", "updated_at"])
    return gym
