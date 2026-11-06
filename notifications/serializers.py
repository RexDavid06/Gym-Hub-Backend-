from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializers import UserSummarySerializer
from notifications.models import Notification, NotificationSetting
from notifications.providers import sms_available, whatsapp_available


class NotificationSettingSerializer(serializers.ModelSerializer):
    sms_available = serializers.SerializerMethodField()
    whatsapp_available = serializers.SerializerMethodField()

    class Meta:
        model = NotificationSetting
        fields = [
            "id",
            "sms_reminders_enabled",
            "sms_receipts_enabled",
            "whatsapp_reminders_enabled",
            "whatsapp_receipts_enabled",
            "sms_available",
            "whatsapp_available",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @extend_schema_field(serializers.BooleanField())
    def get_sms_available(self, setting):
        return sms_available()

    @extend_schema_field(serializers.BooleanField())
    def get_whatsapp_available(self, setting):
        return whatsapp_available()

    def validate(self, attrs):
        instance = self.instance or NotificationSetting()
        sms_reminders = attrs.get(
            "sms_reminders_enabled", getattr(instance, "sms_reminders_enabled", False)
        )
        sms_receipts = attrs.get(
            "sms_receipts_enabled", getattr(instance, "sms_receipts_enabled", False)
        )
        whatsapp_reminders = attrs.get(
            "whatsapp_reminders_enabled",
            getattr(instance, "whatsapp_reminders_enabled", False),
        )
        whatsapp_receipts = attrs.get(
            "whatsapp_receipts_enabled",
            getattr(instance, "whatsapp_receipts_enabled", False),
        )

        errors = {}
        if (sms_reminders or sms_receipts) and not sms_available():
            errors["sms_reminders_enabled"] = "SMS is not available for this environment."
        if (whatsapp_reminders or whatsapp_receipts) and not whatsapp_available():
            errors["whatsapp_reminders_enabled"] = "WhatsApp is not available for this environment."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class NotificationPreferenceSerializer(serializers.Serializer):
    whatsapp_opt_in = serializers.BooleanField()


class NotificationSerializer(serializers.ModelSerializer):
    recipient = UserSummarySerializer(read_only=True, allow_null=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "recipient",
            "recipient_phone",
            "event",
            "schedule_key",
            "channel",
            "status",
            "payload",
            "body",
            "template_name",
            "provider",
            "provider_message_id",
            "attempts",
            "last_error",
            "skip_reason",
            "scheduled_at",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
