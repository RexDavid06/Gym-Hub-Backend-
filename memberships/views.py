from datetime import timedelta
from decimal import Decimal

from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.tenant import GymScopedModelViewSet
from memberships.models import (
    MembershipPayment,
    MembershipPlan,
    MembershipSubscription,
    end_date_for,
    expire_stale_memberships,
)
from memberships.permissions import CanManageMemberships
from memberships.serializers import (
    MembershipPaymentSerializer,
    MembershipPlanSerializer,
    MembershipSubscriptionSerializer,
    MyMembershipSerializer,
    RenewMembershipSerializer,
    VoidPaymentSerializer,
)


def get_gym_member(request, member_id):
    return get_object_or_404(
        User.objects.filter(gym_id=request.user.gym_id, role=User.Role.MEMBER, is_active=True),
        pk=member_id,
    )


def get_gym_plan(request, plan_id, active_only=False):
    queryset = MembershipPlan.objects.filter(gym_id=request.user.gym_id)
    if active_only:
        queryset = queryset.filter(is_active=True)
    return get_object_or_404(queryset, pk=plan_id)


class MembershipPlanViewSet(GymScopedModelViewSet):
    queryset = MembershipPlan.objects.all()
    serializer_class = MembershipPlanSerializer
    permission_classes = [IsAuthenticated, CanManageMemberships]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(request=MembershipPlanSerializer, responses=MembershipPlanSerializer)
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.save(gym=request.user.gym)
        return Response(
            self.get_serializer(plan).data,
            status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )

    def destroy(self, request, *args, **kwargs):
        plan = self.get_object()
        try:
            plan.delete()
        except ProtectedError:
            return Response(
                {"detail": "This plan is in use. Deactivate it instead of deleting it."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembershipSubscriptionViewSet(GymScopedModelViewSet):
    queryset = MembershipSubscription.objects.all()
    serializer_class = MembershipSubscriptionSerializer
    permission_classes = [IsAuthenticated, CanManageMemberships]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("member", "plan", "created_by")
            .prefetch_related("payments")
        )
        expire_stale_memberships(queryset)
        member_id = self.request.query_params.get("member")
        if member_id:
            try:
                member_id = int(member_id)
            except ValueError:
                return queryset.none()
            queryset = queryset.filter(member_id=member_id)
        return queryset

    @extend_schema(
        parameters=[OpenApiParameter("member", int, OpenApiParameter.QUERY, required=False)]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=MembershipSubscriptionSerializer,
        responses=MembershipSubscriptionSerializer,
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_gym_member(request, serializer.validated_data["member_id"])
        plan = get_gym_plan(request, serializer.validated_data["plan_id"], active_only=True)
        if MembershipSubscription.objects.filter(member=member).exists():
            return Response(
                {"detail": "This member already has a membership. Renew it instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        start_date = serializer.validated_data.get("start_date") or timezone.localdate()
        subscription = MembershipSubscription.objects.create(
            gym=request.user.gym,
            member=member,
            plan=plan,
            start_date=start_date,
            end_date=end_date_for(start_date, plan.duration_days),
            price=plan.price,
            created_by=request.user,
        )
        return Response(self.get_serializer(subscription).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=RenewMembershipSerializer, responses=MembershipSubscriptionSerializer)
    def renew(self, request, pk):
        subscription = self.get_object()
        subscription.refresh_status()
        serializer = RenewMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan_id = serializer.validated_data.get("plan_id", subscription.plan_id)
        plan = get_gym_plan(request, plan_id, active_only=True)
        today = timezone.localdate()
        if (
            subscription.status == MembershipSubscription.Status.ACTIVE
            and subscription.end_date >= today
        ):
            start_date = subscription.end_date + timedelta(days=1)
        else:
            start_date = today
        subscription.plan = plan
        subscription.price = plan.price
        subscription.start_date = start_date
        subscription.end_date = end_date_for(start_date, plan.duration_days)
        subscription.status = MembershipSubscription.Status.ACTIVE
        subscription.save()
        return Response(self.get_serializer(subscription).data)

    @extend_schema(request=None, responses=MembershipSubscriptionSerializer)
    def cancel(self, request, pk):
        subscription = self.get_object()
        subscription.refresh_status()
        subscription.status = MembershipSubscription.Status.CANCELLED
        subscription.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(subscription).data)


class MembershipPaymentViewSet(GymScopedModelViewSet):
    queryset = MembershipPayment.objects.all()
    serializer_class = MembershipPaymentSerializer
    permission_classes = [IsAuthenticated, CanManageMemberships]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset().select_related("member", "subscription")
        member_id = self.request.query_params.get("member")
        subscription_id = self.request.query_params.get("subscription")
        if member_id:
            try:
                queryset = queryset.filter(member_id=int(member_id))
            except ValueError:
                return queryset.none()
        if subscription_id:
            try:
                queryset = queryset.filter(subscription_id=int(subscription_id))
            except ValueError:
                return queryset.none()
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter("member", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("subscription", int, OpenApiParameter.QUERY, required=False),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(request=MembershipPaymentSerializer, responses=MembershipPaymentSerializer)
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        member = get_gym_member(request, data["member_id"])
        subscription = None
        subscription_id = data.get("subscription_id")
        if subscription_id is not None:
            subscription = get_object_or_404(
                MembershipSubscription.objects.filter(gym_id=request.user.gym_id, member=member),
                pk=subscription_id,
            )
        amount = data.get("amount")
        if amount is None:
            if subscription is None:
                return Response(
                    {"amount": ["This field is required when no membership is selected."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            amount = subscription.price
        if amount <= Decimal("0"):
            return Response(
                {"amount": ["The amount must be greater than zero."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment = MembershipPayment.objects.create(
            gym=request.user.gym,
            member=member,
            subscription=subscription,
            amount=amount,
            method=data.get("method", MembershipPayment.Method.CASH),
            reference=data.get("reference", ""),
            paid_at=data.get("paid_at") or timezone.localdate(),
            notes=data.get("notes", ""),
            recorded_by=request.user,
        )
        return Response(self.get_serializer(payment).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=VoidPaymentSerializer, responses=MembershipPaymentSerializer)
    def void(self, request, pk):
        """Void a payment. Payments are never edited or deleted."""
        payment = self.get_object()
        if payment.is_voided:
            return Response(
                {"detail": "This payment is already voided."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = VoidPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment.void(by=request.user, reason=serializer.validated_data["reason"])
        return Response(self.get_serializer(payment).data)


class MyMembershipView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MyMembershipSerializer)
    def get(self, request):
        if request.user.role != User.Role.MEMBER:
            return Response(
                {"detail": "Only members have a membership record."},
                status=status.HTTP_403_FORBIDDEN,
            )
        subscription = (
            MembershipSubscription.objects.filter(gym_id=request.user.gym_id, member=request.user)
            .select_related("member", "plan", "created_by")
            .first()
        )
        if subscription:
            subscription.refresh_status()
        payments = MembershipPayment.objects.filter(
            gym_id=request.user.gym_id, member=request.user
        ).order_by("-paid_at", "-id")
        return Response(
            MyMembershipSerializer({"subscription": subscription, "payments": payments}).data
        )
