"""Design intent for Phase 8 Milestone 1 (ratified spec).

Responsibility: represent the tenant-scoped notification preferences,
the notification queue itself, and the hook points that feed Celery
workers. Nothing here sends a byte; Celery workers do that.

Rules
=====
1. Every tenant-owned model has a `gym` FK. `GymFilterableManager` is
   the default manager so all queries are automatically scoped to one
   gym. API code must scope through `GymScopedQuerysetMixin`, never
   cross gyms.
2. Permissions are enforced in the backend (DRF permission classes),
   never only in the UI.
3. Members opt into WhatsApp per gym; staff manages it. SMS has no
   consent field.
4. Row-level dedupe: one `Notification` row per (gym, dedupe_key).
   Workers use a row lock so a single notification is handed to one
   worker at a time.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.tenant import GymFilterableManager, GymOwnedModel
from gyms.models import Gym


class NotificationSetting(GymOwnedModel):
    """Tenant-level feature flags for Phase 8 delivery.

    Read-only to users; mutated only by the owner-facing API.
    """

    gym = models.OneToOneField(
        Gym,
        on_delete=models.CASCADE,
        related_name="notification_setting",
    )
    sms_reminders_enabled = models.BooleanField(default=False)
    sms_receipts_enabled = models.BooleanField(default=False)
    whatsapp_reminders_enabled = models.BooleanField(default=False)
    whatsapp_receipts_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GymFilterableManager()

    class Meta:
        verbose_name = "notification setting"
        verbose_name_plural = "notification settings"

    def __str__(self):
        return f"NotificationSetting({self.gym})"


class NotificationPreference(GymOwnedModel):
    """Per-member opt-in for Phase 8 WhatsApp messages (consent)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    whatsapp_opt_in = models.BooleanField(
        default=False,
        help_text="Member has consented to receive WhatsApp messages from this gym.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GymFilterableManager()

    class Meta:
        verbose_name = "notification preference"
        verbose_name_plural = "notification preferences"

    def __str__(self):
        return f"NotificationPreference({self.user})"


class Notification(GymOwnedModel):
    """A single notification scheduled for delivery.

    Lifecycle: PENDING -> SENDING -> (SENT | FAILED | SKIPPED).
    Uniquely identified per gym by `dedupe_key`.
    """

    class Event(models.TextChoices):
        MEMBERSHIP_EXPIRING = "membership_expiring", "Membership expiring"
        MEMBERSHIP_EXPIRED = "membership_expired", "Membership expired"
        PAYMENT_RECEIPT = "payment_receipt", "Payment receipt"

    class ScheduleKey(models.TextChoices):
        THREE_MONTHS = "3m", "Three months"
        ONE_MONTH = "1m", "One month"
        SEVEN_DAYS = "7d", "Seven days"
        THREE_DAYS = "3d", "Three days"
        DUE = "due", "Due date"
        SEVEN_DAYS_AFTER = "7d_after", "Seven days after"
        RECEIPT = "receipt", "Receipt"

    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    recipient_phone = models.CharField(max_length=32)
    event = models.CharField(max_length=32, choices=Event.choices)
    schedule_key = models.CharField(max_length=20, choices=ScheduleKey.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    dedupe_key = models.CharField(max_length=240)
    payload = models.JSONField(default=dict, blank=True)
    body = models.TextField(blank=True)
    template_name = models.CharField(max_length=100, blank=True)
    provider = models.CharField(max_length=50, blank=True)
    provider_message_id = models.CharField(max_length=200, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    skip_reason = models.CharField(max_length=100, blank=True)
    scheduled_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GymFilterableManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "dedupe_key"],
                name="unique_notification_dedupe_per_gym",
            )
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "scheduled_at"],
                name="notif_gym_status_sched_idx",
            ),
            models.Index(
                fields=["gym", "event", "created_at"],
                name="notif_gym_event_created_idx",
            ),
            models.Index(
                fields=["gym", "channel", "status"],
                name="notif_gym_channel_status_idx",
            ),
        ]

    def __str__(self):
        return f"{self.channel} {self.event} {self.recipient_phone}"

    @property
    def is_sendable(self):
        return self.status in {self.Status.PENDING, self.Status.SENDING}
