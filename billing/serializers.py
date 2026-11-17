from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from billing.models import (
    BillingPayment,
    BillingPlan,
    BillingPlanPrice,
    DunningAttempt,
    GymBillingSubscription,
    Invoice,
)


class GymRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class UserRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField()


class InvoiceRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.CharField()


def gym_ref(gym):
    return {"id": gym.pk, "name": gym.name}


class BillingPlanPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingPlanPrice
        fields = [
            "id",
            "interval",
            "duration_months",
            "currency",
            "amount",
            "effective_from",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "duration_months", "created_at"]

    def validate_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("The amount cannot be negative.")
        return value


class BillingPlanSerializer(serializers.ModelSerializer):
    prices = BillingPlanPriceSerializer(many=True, read_only=True)

    class Meta:
        model = BillingPlan
        fields = [
            "id",
            "name",
            "tier",
            "description",
            "is_active",
            "is_public",
            "prices",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class BillingPlanWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingPlan
        fields = ["id", "name", "tier", "description", "is_active", "is_public"]
        read_only_fields = ["id"]


class SubscriptionPlanSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    tier = serializers.CharField()


class SubscriptionPriceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    interval = serializers.CharField()
    duration_months = serializers.IntegerField()
    currency = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)


class GymBillingSubscriptionSerializer(serializers.ModelSerializer):
    gym = GymRefSerializer(read_only=True)
    plan = SubscriptionPlanSerializer(read_only=True)
    price = SubscriptionPriceSerializer(read_only=True)
    balance = serializers.SerializerMethodField()

    class Meta:
        model = GymBillingSubscription
        fields = [
            "id",
            "gym",
            "plan",
            "price",
            "interval",
            "status",
            "started_at",
            "current_period_start",
            "current_period_end",
            "cancel_at_period_end",
            "balance",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_balance(self, obj):
        total = Decimal("0.00")
        for invoice in obj.invoices.filter(
            status__in=[Invoice.Status.OPEN, Invoice.Status.OVERDUE]
        ):
            total += invoice.balance
        return total


class SubscriptionCreateSerializer(serializers.Serializer):
    gym_id = serializers.IntegerField()
    plan_id = serializers.IntegerField()
    interval = serializers.ChoiceField(choices=BillingPlanPrice.Interval.choices)
    status = serializers.ChoiceField(
        choices=GymBillingSubscription.Status.choices,
        required=False,
        default=GymBillingSubscription.Status.ACTIVE,
    )
    start = serializers.DateField(required=False)


class SubscriptionChangePlanSerializer(serializers.Serializer):
    plan_id = serializers.IntegerField(required=False)
    interval = serializers.ChoiceField(choices=BillingPlanPrice.Interval.choices, required=False)


class InvoiceSerializer(serializers.ModelSerializer):
    gym = GymRefSerializer(read_only=True)
    balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    is_overpaid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "gym",
            "plan",
            "plan_name",
            "interval",
            "status",
            "issued_at",
            "due_at",
            "period_start",
            "period_end",
            "description",
            "amount",
            "amount_paid",
            "balance",
            "is_overpaid",
            "currency",
            "notes",
            "created_at",
            "voided_at",
            "void_reason",
        ]
        read_only_fields = fields


class InvoiceCreateSerializer(serializers.Serializer):
    gym_id = serializers.IntegerField()
    subscription_id = serializers.IntegerField(required=False, allow_null=True)


class MarkPaidSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.01")
    )
    method = serializers.ChoiceField(
        choices=BillingPayment.Method.choices,
        required=False,
        default=BillingPayment.Method.TRANSFER,
    )
    reference = serializers.CharField(required=False, allow_blank=True)


class BillingPaymentSerializer(serializers.ModelSerializer):
    gym = GymRefSerializer(read_only=True)
    invoice = InvoiceRefSerializer(read_only=True)
    plan_name = serializers.SerializerMethodField()
    is_voided = serializers.BooleanField(read_only=True)
    recorded_by = UserRefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = BillingPayment
        fields = [
            "id",
            "gym",
            "invoice",
            "plan_name",
            "amount",
            "method",
            "provider",
            "provider_reference",
            "reference",
            "paid_at",
            "recorded_by",
            "voided_at",
            "void_reason",
            "is_voided",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_plan_name(self, obj):
        return obj.plan.name if obj.plan_id else ""


class PaymentCreateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.01")
    )
    method = serializers.ChoiceField(
        choices=BillingPayment.Method.choices,
        required=False,
        default=BillingPayment.Method.TRANSFER,
    )
    reference = serializers.CharField(required=False, allow_blank=True)


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=False)


class DunningAttemptSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    gym = serializers.SerializerMethodField()

    class Meta:
        model = DunningAttempt
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "gym",
            "offset_days",
            "channel",
            "status",
            "sent_at",
            "error",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(GymRefSerializer)
    def get_gym(self, obj):
        return gym_ref(obj.invoice.gym)


class PaginatedBillingSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True, required=False)
    previous = serializers.CharField(allow_null=True, required=False)


class PaginatedSubscriptionSerializer(PaginatedBillingSerializer):
    results = GymBillingSubscriptionSerializer(many=True)


class PaginatedInvoiceSerializer(PaginatedBillingSerializer):
    results = InvoiceSerializer(many=True)


class PaginatedPaymentSerializer(PaginatedBillingSerializer):
    results = BillingPaymentSerializer(many=True)


class PaginatedDunningSerializer(PaginatedBillingSerializer):
    results = DunningAttemptSerializer(many=True)


class PlatformBillingSummarySerializer(serializers.Serializer):
    active_subscriptions = serializers.IntegerField()
    subscriptions_by_tier = serializers.DictField(child=serializers.IntegerField())
    monthly_recurring_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    outstanding_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    overdue_invoice_count = serializers.IntegerField()
    collected_this_month = serializers.DecimalField(max_digits=12, decimal_places=2)


class CheckoutSerializer(serializers.Serializer):
    authorization_url = serializers.URLField()
    reference = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
