from rest_framework import serializers

from gyms.models import Gym


class PlatformUserSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    phone = serializers.CharField()


class PlatformSummarySerializer(serializers.Serializer):
    gyms_total = serializers.IntegerField()
    gyms_active = serializers.IntegerField()
    gyms_suspended = serializers.IntegerField()
    members_total = serializers.IntegerField()
    trainers_total = serializers.IntegerField()
    active_memberships = serializers.IntegerField()
    memberships_expiring_7_days = serializers.IntegerField()


class PlatformGymListSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    plan_tier = serializers.CharField()
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    owner = PlatformUserSummarySerializer(allow_null=True)
    member_count = serializers.IntegerField()
    trainer_count = serializers.IntegerField()


class PaginatedPlatformGymListSerializer(serializers.Serializer):
    """The page-number paginated shape returned by the gym list endpoint."""

    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = PlatformGymListSerializer(many=True)


class PlatformGymDetailSerializer(PlatformGymListSerializer):
    admin_count = serializers.IntegerField()
    active_membership_count = serializers.IntegerField()
    expired_membership_count = serializers.IntegerField()
    suspended_at = serializers.DateTimeField(allow_null=True)
    suspended_by = PlatformUserSummarySerializer(allow_null=True)
    suspension_reason = serializers.CharField()


class PlatformGymUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    plan_tier = serializers.ChoiceField(choices=Gym.PlanTier.choices, required=False)
    suspension_reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs.get("is_active") is False and not attrs.get("suspension_reason", "").strip():
            raise serializers.ValidationError(
                {"suspension_reason": ["A suspension reason is required."]}
            )
        return attrs
