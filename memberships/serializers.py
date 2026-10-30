from decimal import Decimal

from rest_framework import serializers

from accounts.serializers import UserSummarySerializer
from memberships.models import MembershipPayment, MembershipPlan, MembershipSubscription


class MembershipPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MembershipPlan
        fields = [
            "id",
            "name",
            "description",
            "price",
            "duration_days",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def validate_name(self, value):
        gym_id = self.context["request"].user.gym_id
        queryset = MembershipPlan.objects.filter(gym_id=gym_id, name=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("A plan with this name already exists.")
        return value

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("The price cannot be negative.")
        return value

    def validate_duration_days(self, value):
        if value < 1:
            raise serializers.ValidationError("The duration must be at least one day.")
        return value


class MembershipSubscriptionSerializer(serializers.ModelSerializer):
    member = UserSummarySerializer(read_only=True)
    plan = MembershipPlanSerializer(read_only=True)
    member_id = serializers.IntegerField(write_only=True, required=True)
    plan_id = serializers.IntegerField(write_only=True, required=True)
    days_left = serializers.IntegerField(read_only=True)

    class Meta:
        model = MembershipSubscription
        fields = [
            "id",
            "member",
            "member_id",
            "plan",
            "plan_id",
            "status",
            "start_date",
            "end_date",
            "price",
            "days_left",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "end_date", "price", "created_at", "updated_at"]


class RenewMembershipSerializer(serializers.Serializer):
    plan_id = serializers.IntegerField(required=False)


class MembershipPaymentSerializer(serializers.ModelSerializer):
    member = UserSummarySerializer(read_only=True)
    member_id = serializers.IntegerField(write_only=True, required=True)
    subscription = serializers.IntegerField(source="subscription_id", read_only=True)
    subscription_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.01")
    )
    is_voided = serializers.BooleanField(read_only=True)

    class Meta:
        model = MembershipPayment
        fields = [
            "id",
            "member",
            "member_id",
            "subscription",
            "subscription_id",
            "amount",
            "method",
            "reference",
            "paid_at",
            "notes",
            "receipt_number",
            "recorded_by",
            "voided_at",
            "voided_by",
            "void_reason",
            "is_voided",
            "created_at",
        ]
        read_only_fields = [
            "receipt_number",
            "recorded_by",
            "voided_at",
            "voided_by",
            "void_reason",
            "created_at",
        ]


class VoidPaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=False)


class MyMembershipSerializer(serializers.Serializer):
    subscription = MembershipSubscriptionSerializer(allow_null=True)
    payments = MembershipPaymentSerializer(many=True)
