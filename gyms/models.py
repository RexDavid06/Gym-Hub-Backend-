from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Gym(models.Model):
    class PlanTier(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        BIG = "big", "Big"

    name = models.CharField(max_length=200)
    plan_tier = models.CharField(max_length=10, choices=PlanTier.choices, default=PlanTier.SMALL)
    is_active = models.BooleanField(default=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gyms_suspended",
    )
    suspension_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def suspend(self, *, by, reason):
        """Suspend the gym. A suspension always records who, when and why."""
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError({"suspension_reason": "A suspension reason is required."})
        self.is_active = False
        self.suspended_at = timezone.now()
        self.suspended_by = by
        self.suspension_reason = reason
        self.save(update_fields=["is_active", "suspended_at", "suspended_by", "suspension_reason"])
        return self

    def reactivate(self):
        """Reactivate the gym and clear the suspension audit fields."""
        self.is_active = True
        self.suspended_at = None
        self.suspended_by = None
        self.suspension_reason = ""
        self.save(update_fields=["is_active", "suspended_at", "suspended_by", "suspension_reason"])
        return self

    def __str__(self):
        return self.name


class Branch(models.Model):
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_branch_name_per_gym")
        ]

    def __str__(self):
        return f"{self.name} ({self.gym})"
