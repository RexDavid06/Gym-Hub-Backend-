"""Platform-owned billing models (Phase 10).

Responsibility: represent what the platform sells (``BillingPlan`` and its
versioned ``BillingPlanPrice`` rows), each gym's ``GymBillingSubscription``,
the ``Invoice``/``BillingPayment`` ledger, and the ``WebhookEvent`` /
``DunningAttempt`` bookkeeping.

Rules
=====
1. These models are **platform-owned**, not tenant-owned. They reference a
   ``gym`` but are reachable only through ``/api/platform/`` by a super admin,
   plus the owner's own gym-scoped read-only/checkout endpoints. They are
   deliberately not ``GymOwnedModel`` and never go through the tenant guard.
2. Invoices and payments are never edited or deleted after creation; they are
   voided with a reason and an actor. Receipt-like discipline, platform-wide.
3. Plans and prices are versioned. A price change adds a new
   ``BillingPlanPrice`` row; existing rows are never edited. Invoices and
   payments snapshot the plan and amount so history is defensible.
4. Money is ``DecimalField(max_digits=10, decimal_places=2)``, matching the
   gym-level memberships app.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from gyms.models import Gym

ZERO = Decimal("0.00")


def add_months(value, months):
    """Add calendar months to a date, clamping the day to the month's length."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def period_end_for(start, duration_months):
    """The inclusive last day of a billing period starting on ``start``."""
    return add_months(start, duration_months) - timedelta(days=1)


class BillingPlan(models.Model):
    class Tier(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        BIG = "big", "Big"

    name = models.CharField(max_length=100)
    tier = models.CharField(max_length=10, choices=Tier.choices)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier", "name"]

    def __str__(self):
        return self.name

    def current_price(self, interval, *, on=None):
        """The active price for ``interval`` effective on or before ``on``."""
        on = on or timezone.localdate()
        return (
            self.prices.filter(interval=interval, is_active=True, effective_from__lte=on)
            .order_by("-effective_from", "-id")
            .first()
        )


class BillingPlanPrice(models.Model):
    class Interval(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "3 months"
        SEMIANNUAL = "semiannual", "6 months"
        ANNUAL = "annual", "Annual"

    INTERVAL_MONTHS = {
        "monthly": 1,
        "quarterly": 3,
        "semiannual": 6,
        "annual": 12,
    }

    plan = models.ForeignKey(BillingPlan, on_delete=models.CASCADE, related_name="prices")
    interval = models.CharField(max_length=20, choices=Interval.choices)
    duration_months = models.PositiveSmallIntegerField(default=1)
    currency = models.CharField(max_length=3, default="NGN")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["plan", "interval", "-effective_from", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "interval", "currency", "effective_from"],
                name="unique_plan_price_version",
            ),
        ]
        indexes = [
            models.Index(fields=["plan", "interval", "effective_from"]),
        ]

    def save(self, *args, **kwargs):
        months = self.INTERVAL_MONTHS.get(self.interval)
        if months:
            self.duration_months = months
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "The amount cannot be negative."})

    def __str__(self):
        return f"{self.plan} {self.interval} {self.amount}"


class GymBillingSubscription(models.Model):
    class Status(models.TextChoices):
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELLED = "cancelled", "Cancelled"

    gym = models.OneToOneField(Gym, on_delete=models.CASCADE, related_name="billing_subscription")
    plan = models.ForeignKey(BillingPlan, on_delete=models.PROTECT, related_name="subscriptions")
    price = models.ForeignKey(
        BillingPlanPrice, on_delete=models.PROTECT, related_name="subscriptions"
    )
    interval = models.CharField(max_length=20, choices=BillingPlanPrice.Interval.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateField(default=timezone.localdate)
    current_period_end = models.DateField()
    cancel_at_period_end = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["status", "current_period_end"]),
        ]

    def __str__(self):
        return f"{self.gym} - {self.plan} ({self.interval})"

    def advance_period(self, *, start=None):
        """Roll the current period forward by the plan's interval."""
        months = self.price.duration_months or BillingPlanPrice.INTERVAL_MONTHS[self.interval]
        start = start or (self.current_period_end + timedelta(days=1))
        self.current_period_start = start
        self.current_period_end = period_end_for(start, months)
        if self.status == self.Status.CANCELLED:
            self.status = self.Status.ACTIVE
        self.save(
            update_fields=["current_period_start", "current_period_end", "status", "updated_at"]
        )
        return self


def next_invoice_number():
    """Allocate the next platform invoice number under a row lock."""
    with transaction.atomic():
        sequence, _ = BillingSequence.objects.select_for_update().get_or_create(name="invoice")
        sequence.value += 1
        sequence.save(update_fields=["value"])
        return f"INV-{timezone.localdate().year}-{sequence.value:05d}"


class BillingSequence(models.Model):
    """A named monotonic counter used to allocate invoice numbers safely."""

    name = models.CharField(max_length=32, unique=True)
    value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.name}={self.value}"


class Invoice(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        VOID = "void", "Void"

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="billing_invoices")
    subscription = models.ForeignKey(
        GymBillingSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    plan = models.ForeignKey(BillingPlan, on_delete=models.PROTECT, related_name="invoices")
    plan_name = models.CharField(max_length=100)
    interval = models.CharField(max_length=20, choices=BillingPlanPrice.Interval.choices)
    number = models.CharField(max_length=32, unique=True, blank=True)
    reference = models.CharField(max_length=120, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    issued_at = models.DateField(default=timezone.localdate)
    due_at = models.DateField()
    period_start = models.DateField()
    period_end = models.DateField()
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=3, default="NGN")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="billing_invoices_created",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="billing_invoices_voided",
    )
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "period_start"],
                name="unique_invoice_per_subscription_period",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="invoice_amount_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status", "due_at"]),
            models.Index(fields=["status", "due_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_invoice_number()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.due_at and self.issued_at and self.due_at < self.issued_at:
            raise ValidationError({"due_at": "The due date cannot be before the issue date."})
        if self.period_end and self.period_start and self.period_end < self.period_start:
            raise ValidationError({"period_end": "The period end cannot be before the start."})

    @property
    def balance(self):
        return max(self.amount - self.amount_paid, ZERO)

    @property
    def is_voided(self):
        return self.status == self.Status.VOID

    @property
    def is_overpaid(self):
        """True when recorded payments exceed the invoice amount (allowed, flagged)."""
        return (self.amount_paid or ZERO) > self.amount

    @property
    def is_overdue(self):
        return (
            self.status in {self.Status.OPEN, self.Status.OVERDUE}
            and self.due_at < timezone.localdate()
        )

    def refresh_overdue(self):
        if self.is_overdue and self.status != self.Status.OVERDUE:
            self.status = self.Status.OVERDUE
            self.save(update_fields=["status"])
        return self.status

    def recompute_status(self):
        """Re-derive the open/paid/overdue status from the amount paid and due date."""
        if self.status == self.Status.VOID:
            return self.status
        if (self.amount_paid or ZERO) >= self.amount:
            self.status = self.Status.PAID
        elif self.due_at < timezone.localdate():
            self.status = self.Status.OVERDUE
        else:
            self.status = self.Status.OPEN
        return self.status

    def apply_payment(self, amount):
        """Apply a payment amount and update the invoice status atomically."""
        if self.status == self.Status.VOID:
            raise ValidationError({"detail": "A voided invoice cannot receive payments."})
        amount = Decimal(amount)
        self.amount_paid = (self.amount_paid or ZERO) + amount
        self.recompute_status()
        self.save(update_fields=["amount_paid", "status"])
        return self

    def void(self, *, by, reason):
        if self.is_voided:
            raise ValidationError({"detail": "This invoice is already voided."})
        if self.amount_paid > ZERO:
            raise ValidationError(
                {"detail": "Void the payments on this invoice before voiding it."}
            )
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError({"reason": "A void reason is required."})
        self.status = self.Status.VOID
        self.voided_at = timezone.now()
        self.voided_by = by
        self.void_reason = reason
        self.save(update_fields=["status", "voided_at", "voided_by", "void_reason"])
        return self

    def __str__(self):
        return self.number


class BillingPayment(models.Model):
    class Method(models.TextChoices):
        CARD = "card", "Card"
        TRANSFER = "transfer", "Bank transfer"
        CASH = "cash", "Cash"
        POS = "pos", "POS"
        OTHER = "other", "Other"

    class Provider(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        MANUAL = "manual", "Manual"

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="billing_payments")
    invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments"
    )
    plan = models.ForeignKey(
        BillingPlan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.TRANSFER)
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.MANUAL)
    provider_reference = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    paid_at = models.DateField(default=timezone.localdate)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="billing_payments_recorded",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="billing_payments_voided",
    )
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_reference"],
                condition=~models.Q(provider_reference=""),
                name="unique_billing_provider_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_payment_amount_positive"
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "paid_at"]),
        ]

    @property
    def is_voided(self):
        return self.voided_at is not None

    def void(self, *, by, reason):
        """Void a payment and reverse its effect on the linked invoice."""
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError({"reason": "A void reason is required."})
        with transaction.atomic():
            payment = BillingPayment.objects.select_for_update().get(pk=self.pk)
            if payment.is_voided:
                raise ValidationError({"detail": "This payment is already voided."})
            payment.voided_at = timezone.now()
            payment.voided_by = by
            payment.void_reason = reason
            payment.save(update_fields=["voided_at", "voided_by", "void_reason"])
            if payment.invoice_id:
                invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
                invoice.amount_paid = max((invoice.amount_paid or ZERO) - payment.amount, ZERO)
                invoice.recompute_status()
                invoice.save(update_fields=["amount_paid", "status"])
        self.voided_at = payment.voided_at
        self.voided_by = payment.voided_by
        self.void_reason = payment.void_reason
        return self

    def __str__(self):
        return f"{self.gym} - {self.amount}"


class WebhookEvent(models.Model):
    """An idempotency record for a provider webhook delivery."""

    provider = models.CharField(max_length=20, default="paystack")
    event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.provider}:{self.event_id}"


class DunningAttempt(models.Model):
    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="dunning_attempts")
    offset_days = models.IntegerField()
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.SMS)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "offset_days", "channel"],
                name="unique_dunning_per_invoice_offset_channel",
            ),
        ]

    def __str__(self):
        return f"{self.invoice} {self.offset_days}d {self.channel}"


BILLING_PAYMENT_METHOD_CHOICES = BillingPayment.Method.choices
BILLING_SUBSCRIPTION_STATUS_CHOICES = GymBillingSubscription.Status.choices
BILLING_PLAN_TIER_CHOICES = BillingPlan.Tier.choices
