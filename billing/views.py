import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, F, ProtectedError, Sum
from django.http import Http404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSuperAdmin
from billing import services
from billing.models import (
    BillingPayment,
    BillingPlan,
    DunningAttempt,
    GymBillingSubscription,
    Invoice,
    WebhookEvent,
)
from billing.paystack import (
    PaystackError,
    billing_email,
    initialize_transaction,
    provider_enabled,
    verify_webhook_signature,
)
from billing.permissions import IsOwnerOfGym
from billing.serializers import (
    BillingPaymentSerializer,
    BillingPlanPriceSerializer,
    BillingPlanSerializer,
    BillingPlanWriteSerializer,
    CheckoutSerializer,
    DunningAttemptSerializer,
    GymBillingSubscriptionSerializer,
    InvoiceCreateSerializer,
    InvoiceSerializer,
    MarkPaidSerializer,
    PaginatedDunningSerializer,
    PaginatedInvoiceSerializer,
    PaginatedPaymentSerializer,
    PaginatedSubscriptionSerializer,
    PaymentCreateSerializer,
    PlatformBillingSummarySerializer,
    SubscriptionChangePlanSerializer,
    SubscriptionCreateSerializer,
    VoidSerializer,
)

PLATFORM_PERMISSIONS = [IsAuthenticated, IsSuperAdmin]
OWNER_PERMISSIONS = [IsAuthenticated, IsOwnerOfGym]
PAGINATION_PARAMETERS = [
    OpenApiParameter("page", int, OpenApiParameter.QUERY, required=False),
    OpenApiParameter("page_size", int, OpenApiParameter.QUERY, required=False),
]


class BillingPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def paginate(view, queryset, serializer_class, request):
    paginator = BillingPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


def get_plan(pk):
    plan = BillingPlan.objects.filter(pk=pk).first()
    if plan is None:
        raise Http404
    return plan


def get_subscription(pk):
    subscription = (
        GymBillingSubscription.objects.select_related("gym", "plan", "price").filter(pk=pk).first()
    )
    if subscription is None:
        raise Http404
    return subscription


def get_invoice(pk):
    invoice = Invoice.objects.select_related("gym", "plan").filter(pk=pk).first()
    if invoice is None:
        raise Http404
    return invoice


def get_payment(pk):
    payment = (
        BillingPayment.objects.select_related("gym", "invoice", "plan", "recorded_by")
        .filter(pk=pk)
        .first()
    )
    if payment is None:
        raise Http404
    return payment


def validation_error(error):
    detail = getattr(error, "message_dict", None) or error.messages
    return Response(detail, status=status.HTTP_400_BAD_REQUEST)


class PlatformBillingPlanListView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_plan_list", responses=BillingPlanSerializer(many=True)
    )
    def get(self, request):
        plans = BillingPlan.objects.prefetch_related("prices").all()
        return Response(BillingPlanSerializer(plans, many=True).data)

    @extend_schema(
        operation_id="platform_billing_plan_create",
        request=BillingPlanWriteSerializer,
        responses=BillingPlanSerializer,
    )
    def post(self, request):
        serializer = BillingPlanWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.save()
        return Response(BillingPlanSerializer(plan).data, status=status.HTTP_201_CREATED)


class PlatformBillingPlanDetailView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(operation_id="platform_billing_plan_detail", responses=BillingPlanSerializer)
    def get(self, request, pk):
        return Response(BillingPlanSerializer(get_plan(pk)).data)

    @extend_schema(
        operation_id="platform_billing_plan_update",
        request=BillingPlanWriteSerializer,
        responses=BillingPlanSerializer,
    )
    def patch(self, request, pk):
        plan = get_plan(pk)
        serializer = BillingPlanWriteSerializer(plan, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(BillingPlanSerializer(plan).data)

    @extend_schema(operation_id="platform_billing_plan_delete", responses={204: None})
    def delete(self, request, pk):
        plan = get_plan(pk)
        try:
            plan.delete()
        except ProtectedError:
            plan.is_active = False
            plan.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlatformBillingPlanPriceView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_plan_price_create",
        request=BillingPlanPriceSerializer,
        responses=BillingPlanPriceSerializer,
    )
    def post(self, request, pk):
        plan = get_plan(pk)
        serializer = BillingPlanPriceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        price = serializer.save(plan=plan)
        return Response(BillingPlanPriceSerializer(price).data, status=status.HTTP_201_CREATED)


class PlatformBillingSubscriptionListView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_subscription_list",
        parameters=[
            *PAGINATION_PARAMETERS,
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("plan", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("gym", int, OpenApiParameter.QUERY, required=False),
        ],
        responses=PaginatedSubscriptionSerializer,
    )
    def get(self, request):
        queryset = GymBillingSubscription.objects.select_related("gym", "plan", "price")
        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        plan_id = request.query_params.get("plan")
        if plan_id:
            queryset = queryset.filter(plan_id=plan_id)
        gym_id = request.query_params.get("gym")
        if gym_id:
            queryset = queryset.filter(gym_id=gym_id)
        queryset = queryset.order_by("-updated_at", "-id")
        return paginate(self, queryset, GymBillingSubscriptionSerializer, request)

    @extend_schema(
        operation_id="platform_billing_subscription_create",
        request=SubscriptionCreateSerializer,
        responses=GymBillingSubscriptionSerializer,
    )
    def post(self, request):
        from gyms.models import Gym

        serializer = SubscriptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gym = Gym.objects.filter(pk=data["gym_id"]).first()
        if gym is None:
            return Response(
                {"gym_id": ["No gym with that id."]}, status=status.HTTP_400_BAD_REQUEST
            )
        if GymBillingSubscription.objects.filter(gym=gym).exists():
            return Response(
                {"gym_id": ["This gym already has a billing subscription."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan = BillingPlan.objects.filter(pk=data["plan_id"]).first()
        if plan is None:
            return Response(
                {"plan_id": ["No plan with that id."]}, status=status.HTTP_400_BAD_REQUEST
            )
        price = plan.current_price(data["interval"])
        if price is None:
            return Response(
                {"interval": ["No active price for this plan and interval."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        subscription = services.create_subscription(
            gym=gym,
            plan=plan,
            price=price,
            status=data["status"],
            created_by=request.user,
            start=data.get("start"),
        )
        return Response(
            GymBillingSubscriptionSerializer(subscription).data,
            status=status.HTTP_201_CREATED,
        )


class PlatformBillingSubscriptionDetailView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_subscription_detail",
        responses=GymBillingSubscriptionSerializer,
    )
    def get(self, request, pk):
        return Response(GymBillingSubscriptionSerializer(get_subscription(pk)).data)


class PlatformBillingSubscriptionChangePlanView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_subscription_change_plan",
        request=SubscriptionChangePlanSerializer,
        responses=GymBillingSubscriptionSerializer,
    )
    def post(self, request, pk):
        subscription = get_subscription(pk)
        serializer = SubscriptionChangePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        interval = data.get("interval", subscription.interval)
        plan = subscription.plan
        if data.get("plan_id"):
            plan = BillingPlan.objects.filter(pk=data["plan_id"]).first()
            if plan is None:
                return Response(
                    {"plan_id": ["No plan with that id."]}, status=status.HTTP_400_BAD_REQUEST
                )
        price = plan.current_price(interval)
        if price is None:
            return Response(
                {"interval": ["No active price for this plan and interval."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        subscription.plan = plan
        subscription.price = price
        subscription.interval = interval
        subscription.save(update_fields=["plan", "price", "interval", "updated_at"])
        return Response(GymBillingSubscriptionSerializer(subscription).data)


class PlatformBillingSubscriptionCancelView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_subscription_cancel",
        request=None,
        responses=GymBillingSubscriptionSerializer,
    )
    def post(self, request, pk):
        subscription = get_subscription(pk)
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
        return Response(GymBillingSubscriptionSerializer(subscription).data)


class PlatformBillingSubscriptionReactivateView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_subscription_reactivate",
        request=None,
        responses=GymBillingSubscriptionSerializer,
    )
    def post(self, request, pk):
        subscription = get_subscription(pk)
        subscription.cancel_at_period_end = False
        if subscription.status == GymBillingSubscription.Status.CANCELLED:
            subscription.status = GymBillingSubscription.Status.ACTIVE
        subscription.save(update_fields=["cancel_at_period_end", "status", "updated_at"])
        return Response(GymBillingSubscriptionSerializer(subscription).data)


class PlatformBillingInvoiceListView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_invoice_list",
        parameters=[
            *PAGINATION_PARAMETERS,
            OpenApiParameter("gym", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False),
        ],
        responses=PaginatedInvoiceSerializer,
    )
    def get(self, request):
        queryset = Invoice.objects.select_related("gym", "plan")
        gym_id = request.query_params.get("gym")
        if gym_id:
            queryset = queryset.filter(gym_id=gym_id)
        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        queryset = queryset.order_by("-issued_at", "-id")
        return paginate(self, queryset, InvoiceSerializer, request)

    @extend_schema(
        operation_id="platform_billing_invoice_create",
        request=InvoiceCreateSerializer,
        responses=InvoiceSerializer,
    )
    def post(self, request):
        from gyms.models import Gym

        serializer = InvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gym = Gym.objects.filter(pk=data["gym_id"]).first()
        if gym is None:
            return Response(
                {"gym_id": ["No gym with that id."]}, status=status.HTTP_400_BAD_REQUEST
            )
        subscription = None
        if data.get("subscription_id"):
            subscription = GymBillingSubscription.objects.filter(
                pk=data["subscription_id"], gym=gym
            ).first()
        else:
            subscription = getattr(gym, "billing_subscription", None)
        if subscription is None:
            return Response(
                {"subscription_id": ["This gym has no billing subscription."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invoice = services.issue_invoice_for_subscription(subscription, created_by=request.user)
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class PlatformBillingInvoiceDetailView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(operation_id="platform_billing_invoice_detail", responses=InvoiceSerializer)
    def get(self, request, pk):
        return Response(InvoiceSerializer(get_invoice(pk)).data)


class PlatformBillingInvoiceVoidView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_invoice_void",
        request=VoidSerializer,
        responses=InvoiceSerializer,
    )
    def post(self, request, pk):
        invoice = get_invoice(pk)
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invoice.void(by=request.user, reason=serializer.validated_data["reason"])
        except ValidationError as error:
            return validation_error(error)
        return Response(InvoiceSerializer(invoice).data)


class PlatformBillingInvoiceMarkPaidView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_invoice_mark_paid",
        request=MarkPaidSerializer,
        responses=BillingPaymentSerializer,
    )
    def post(self, request, pk):
        invoice = get_invoice(pk)
        serializer = MarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        amount = data.get("amount") or invoice.balance
        try:
            payment = services.record_payment(
                invoice=invoice,
                amount=amount,
                method=data.get("method", BillingPayment.Method.TRANSFER),
                reference=data.get("reference", ""),
                recorded_by=request.user,
            )
        except ValidationError as error:
            return validation_error(error)
        return Response(BillingPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class PlatformBillingInvoiceDunView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_invoice_dun",
        request=None,
        responses=DunningAttemptSerializer,
    )
    def post(self, request, pk):
        from billing.tasks import _send_dunning

        invoice = get_invoice(pk)
        offset = (invoice.due_at - timezone.localdate()).days
        attempt, _ = services.create_dunning_attempt(invoice, offset)
        _send_dunning(invoice, attempt)
        attempt.refresh_from_db()
        return Response(DunningAttemptSerializer(attempt).data)


class PlatformBillingPaymentListView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_payment_list",
        parameters=[
            *PAGINATION_PARAMETERS,
            OpenApiParameter("gym", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("invoice", int, OpenApiParameter.QUERY, required=False),
        ],
        responses=PaginatedPaymentSerializer,
    )
    def get(self, request):
        queryset = BillingPayment.objects.select_related("gym", "invoice", "plan", "recorded_by")
        gym_id = request.query_params.get("gym")
        if gym_id:
            queryset = queryset.filter(gym_id=gym_id)
        invoice_id = request.query_params.get("invoice")
        if invoice_id:
            queryset = queryset.filter(invoice_id=invoice_id)
        queryset = queryset.order_by("-paid_at", "-id")
        return paginate(self, queryset, BillingPaymentSerializer, request)

    @extend_schema(
        operation_id="platform_billing_payment_create",
        request=PaymentCreateSerializer,
        responses=BillingPaymentSerializer,
    )
    def post(self, request):
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = Invoice.objects.filter(pk=data["invoice_id"]).first()
        if invoice is None:
            return Response(
                {"invoice_id": ["No invoice with that id."]}, status=status.HTTP_400_BAD_REQUEST
            )
        amount = data.get("amount") or invoice.balance
        try:
            payment = services.record_payment(
                invoice=invoice,
                amount=amount,
                method=data.get("method", BillingPayment.Method.TRANSFER),
                reference=data.get("reference", ""),
                recorded_by=request.user,
            )
        except ValidationError as error:
            return validation_error(error)
        return Response(BillingPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class PlatformBillingPaymentDetailView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_payment_detail", responses=BillingPaymentSerializer
    )
    def get(self, request, pk):
        return Response(BillingPaymentSerializer(get_payment(pk)).data)


class PlatformBillingPaymentVoidView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_payment_void",
        request=VoidSerializer,
        responses=BillingPaymentSerializer,
    )
    def post(self, request, pk):
        payment = get_payment(pk)
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payment.void(by=request.user, reason=serializer.validated_data["reason"])
        except ValidationError as error:
            return validation_error(error)
        return Response(BillingPaymentSerializer(payment).data)


class PlatformBillingDunningListView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_dunning_list",
        parameters=[
            *PAGINATION_PARAMETERS,
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False),
        ],
        responses=PaginatedDunningSerializer,
    )
    def get(self, request):
        queryset = DunningAttempt.objects.select_related("invoice", "invoice__gym")
        status_param = request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        queryset = queryset.order_by("-created_at", "-id")
        return paginate(self, queryset, DunningAttemptSerializer, request)


class PlatformBillingSummaryView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_billing_summary", responses=PlatformBillingSummarySerializer
    )
    def get(self, request):
        today = timezone.localdate()
        active = GymBillingSubscription.objects.filter(status=GymBillingSubscription.Status.ACTIVE)
        by_tier = {
            row["plan__tier"]: row["count"]
            for row in active.values("plan__tier").annotate(count=Count("id"))
        }
        mrr = Decimal("0.00")
        # Group by price so the query stays bounded by distinct plans, not gyms.
        price_rows = active.values("price__duration_months", "price__amount").annotate(
            count=Count("id")
        )
        for row in price_rows:
            months = row["price__duration_months"] or 1
            mrr += (row["price__amount"] / months) * row["count"]
        invoices = Invoice.objects.filter(status__in=[Invoice.Status.OPEN, Invoice.Status.OVERDUE])
        outstanding = invoices.aggregate(total=Sum(F("amount") - F("amount_paid")))[
            "total"
        ] or Decimal("0.00")
        collected = BillingPayment.objects.filter(
            voided_at__isnull=True, paid_at__gte=today.replace(day=1)
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        data = {
            "active_subscriptions": active.count(),
            "subscriptions_by_tier": by_tier,
            "monthly_recurring_revenue": mrr.quantize(Decimal("0.01")),
            "outstanding_balance": outstanding,
            "overdue_invoice_count": Invoice.objects.filter(status=Invoice.Status.OVERDUE).count(),
            "collected_this_month": collected,
        }
        return Response(PlatformBillingSummarySerializer(data).data)


@method_decorator(csrf_exempt, name="dispatch")
class PaystackWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="billing_paystack_webhook",
        request=None,
        responses={200: None},
        auth=[],
    )
    def post(self, request):
        signature = request.headers.get("x-paystack-signature", "")
        if not verify_webhook_signature(request.body, signature):
            return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST)
        from billing.tasks import process_paystack_webhook

        data = payload.get("data") or {}
        event_type = payload.get("event", "")
        event_id = str(data.get("id") or data.get("reference") or "")
        if not event_id:
            return Response({"detail": "Missing event id."}, status=status.HTTP_400_BAD_REQUEST)
        event, created = WebhookEvent.objects.get_or_create(
            event_id=event_id,
            defaults={"event_type": event_type, "payload": payload},
        )
        if not created:
            return Response({"status": "duplicate"})
        process_paystack_webhook.delay(event.pk)
        return Response({"status": "ok"})


class OwnerBillingSubscriptionView(APIView):
    permission_classes = OWNER_PERMISSIONS

    @extend_schema(
        operation_id="owner_billing_subscription", responses=GymBillingSubscriptionSerializer
    )
    def get(self, request):
        subscription = (
            GymBillingSubscription.objects.filter(gym_id=request.user.gym_id)
            .select_related("gym", "plan", "price")
            .first()
        )
        if subscription is None:
            raise Http404
        return Response(GymBillingSubscriptionSerializer(subscription).data)


class OwnerBillingInvoiceListView(APIView):
    permission_classes = OWNER_PERMISSIONS

    @extend_schema(
        operation_id="owner_billing_invoice_list",
        parameters=PAGINATION_PARAMETERS,
        responses=PaginatedInvoiceSerializer,
    )
    def get(self, request):
        queryset = Invoice.objects.filter(gym_id=request.user.gym_id).select_related("gym", "plan")
        return paginate(self, queryset, InvoiceSerializer, request)


class OwnerBillingInvoiceDetailView(APIView):
    permission_classes = OWNER_PERMISSIONS

    @extend_schema(operation_id="owner_billing_invoice_detail", responses=InvoiceSerializer)
    def get(self, request, pk):
        invoice = Invoice.objects.filter(pk=pk, gym_id=request.user.gym_id).first()
        if invoice is None:
            raise Http404
        return Response(InvoiceSerializer(invoice).data)


class OwnerBillingInvoiceCheckoutView(APIView):
    permission_classes = OWNER_PERMISSIONS

    @extend_schema(
        operation_id="owner_billing_invoice_checkout",
        request=None,
        responses=CheckoutSerializer,
    )
    def post(self, request, pk):
        invoice = Invoice.objects.filter(pk=pk, gym_id=request.user.gym_id).first()
        if invoice is None:
            raise Http404
        if invoice.status in {Invoice.Status.PAID, Invoice.Status.VOID}:
            return Response(
                {"detail": "This invoice does not need payment."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not provider_enabled():
            return Response(
                {"detail": "Online payment is not enabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reference = f"gymhub-{invoice.pk}-{uuid.uuid4().hex[:12]}"
        try:
            result = initialize_transaction(
                email=billing_email(invoice.gym_id),
                amount=invoice.balance,
                reference=reference,
                metadata={"invoice_id": invoice.pk},
            )
        except PaystackError as error:
            return Response({"detail": str(error)}, status=status.HTTP_502_BAD_GATEWAY)
        invoice.reference = result["reference"] or reference
        invoice.save(update_fields=["reference"])
        data = {
            "authorization_url": result["authorization_url"],
            "reference": invoice.reference,
            "amount": invoice.balance,
        }
        return Response(CheckoutSerializer(data).data)


class OwnerBillingPaymentListView(APIView):
    permission_classes = OWNER_PERMISSIONS

    @extend_schema(
        operation_id="owner_billing_payment_list",
        parameters=PAGINATION_PARAMETERS,
        responses=PaginatedPaymentSerializer,
    )
    def get(self, request):
        queryset = BillingPayment.objects.filter(gym_id=request.user.gym_id).select_related(
            "gym", "invoice", "plan", "recorded_by"
        )
        return paginate(self, queryset, BillingPaymentSerializer, request)
