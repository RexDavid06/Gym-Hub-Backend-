from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from accounts.tenant import GymOwnedModel


class MembershipPlan(GymOwnedModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration_days = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "name"], name="unique_membership_plan_name_per_gym"
            )
        ]

    def clean(self):
        super().clean()
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": "The price cannot be negative."})
        if self.duration_days is not None and self.duration_days < 1:
            raise ValidationError({"duration_days": "The duration must be at least one day."})

    def __str__(self):
        return self.name


class MembershipSubscription(GymOwnedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    member = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membership_subscription"
    )
    plan = models.ForeignKey(MembershipPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="memberships_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="membership_end_after_start",
            )
        ]

    def clean(self):
        super().clean()
        if self.gym_id and self.member_id and self.member.gym_id != self.gym_id:
            raise ValidationError({"member": "The member must belong to the membership's gym."})
        if self.plan_id and self.gym_id and self.plan.gym_id != self.gym_id:
            raise ValidationError({"plan": "The plan must belong to the membership's gym."})
        if self.member_id and self.member.role != "member":
            raise ValidationError({"member": "Memberships can only belong to members."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date must be on or after the start date."})

    def refresh_status(self):
        if self.status == self.Status.ACTIVE and self.end_date < timezone.localdate():
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status", "updated_at"])
        return self.status

    @property
    def days_left(self):
        if self.status != self.Status.ACTIVE:
            return 0
        return max((self.end_date - timezone.localdate()).days + 1, 0)

    def __str__(self):
        return f"{self.member} - {self.plan}"


def end_date_for(start_date, duration_days):
    return start_date + timedelta(days=duration_days - 1)


def expire_stale_memberships(queryset):
    queryset.filter(
        status=MembershipSubscription.Status.ACTIVE, end_date__lt=timezone.localdate()
    ).update(status=MembershipSubscription.Status.EXPIRED, updated_at=timezone.now())
    return queryset


def next_receipt_number(gym_id):
    """Return the next sequential receipt number for a gym.

    The gym row is locked so concurrent payments in the same gym cannot allocate the
    same number. Numbers are never reused because payments are voided, never deleted.
    """
    from gyms.models import Gym

    with transaction.atomic():
        Gym.objects.select_for_update().filter(pk=gym_id).first()
        used = MembershipPayment.objects.filter(gym_id=gym_id).count()
        return f"RC-{gym_id:04d}-{used + 1:05d}"


class MembershipPayment(GymOwnedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        TRANSFER = "transfer", "Bank transfer"
        POS = "pos", "POS"
        OTHER = "other", "Other"

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membership_payments"
    )
    subscription = models.ForeignKey(
        MembershipSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    receipt_number = models.CharField(max_length=32, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="membership_payments_recorded",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="membership_payments_voided",
    )
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "receipt_number"], name="unique_receipt_number_per_gym"
            )
        ]

    def clean(self):
        super().clean()
        if self.gym_id and self.member_id and self.member.gym_id != self.gym_id:
            raise ValidationError({"member": "The member must belong to the payment's gym."})
        if self.subscription_id and self.gym_id and self.subscription.gym_id != self.gym_id:
            raise ValidationError({"subscription": "The membership must belong to the same gym."})
        if self.subscription_id and self.member_id:
            if self.subscription.member_id != self.member_id:
                raise ValidationError(
                    {"subscription": "The membership must belong to the selected member."}
                )
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "The amount must be greater than zero."})

    @property
    def is_voided(self):
        return self.voided_at is not None

    def void(self, *, by, reason):
        """Void a payment. Payments are never edited or deleted, only voided."""
        if self.is_voided:
            raise ValidationError({"void_reason": "This payment is already voided."})
        self.voided_at = timezone.now()
        self.voided_by = by
        self.void_reason = reason
        self.save(update_fields=["voided_at", "voided_by", "void_reason"])
        return self

    def save(self, *args, **kwargs):
        if not self.receipt_number and self.gym_id:
            self.receipt_number = next_receipt_number(self.gym_id)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member} - {self.amount}"
