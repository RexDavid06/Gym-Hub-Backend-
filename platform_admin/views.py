from datetime import timedelta

from django.db.models import Count, Q
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsSuperAdmin
from gyms.models import Gym
from memberships.models import MembershipSubscription
from platform_admin.serializers import (
    PaginatedPlatformGymListSerializer,
    PlatformGymDetailSerializer,
    PlatformGymListSerializer,
    PlatformGymUpdateSerializer,
    PlatformSummarySerializer,
    PlatformUserSummarySerializer,
)

PLATFORM_PERMISSIONS = [IsAuthenticated, IsSuperAdmin]
EXPIRING_WINDOW_DAYS = 7


def serialize_user(user):
    if user is None:
        return None
    return PlatformUserSummarySerializer(
        {"id": user.pk, "name": user.name, "phone": user.phone}
    ).data


def owners_for(gym_ids):
    """Map gym id to its earliest active owner (by date_joined, then id)."""
    owners = {}
    rows = User.objects.filter(gym_id__in=gym_ids, role=User.Role.OWNER, is_active=True).order_by(
        "gym_id", "date_joined", "id"
    )
    for row in rows:
        owners.setdefault(row.gym_id, row)
    return owners


def with_user_counts(queryset):
    return queryset.annotate(
        member_count=Count(
            "users",
            filter=Q(users__role=User.Role.MEMBER, users__is_active=True),
            distinct=True,
        ),
        trainer_count=Count(
            "users",
            filter=Q(users__role=User.Role.TRAINER, users__is_active=True),
            distinct=True,
        ),
    )


def active_memberships():
    """Subscriptions that are active as of today; an ended date means expired."""
    return MembershipSubscription.objects.filter(
        status=MembershipSubscription.Status.ACTIVE,
        end_date__gte=timezone.localdate(),
    )


def serialize_gym(gym, owner, *, detail=False):
    data = {
        "id": gym.pk,
        "name": gym.name,
        "plan_tier": gym.plan_tier,
        "is_active": gym.is_active,
        "created_at": gym.created_at,
        "owner": serialize_user(owner),
        "member_count": getattr(gym, "member_count", 0),
        "trainer_count": getattr(gym, "trainer_count", 0),
    }
    if not detail:
        return PlatformGymListSerializer(data).data
    today = timezone.localdate()
    data.update(
        {
            "admin_count": User.objects.filter(
                gym=gym, role=User.Role.ADMIN, is_active=True
            ).count(),
            "active_membership_count": active_memberships().filter(gym=gym).count(),
            "expired_membership_count": MembershipSubscription.objects.filter(gym=gym)
            .filter(
                Q(status=MembershipSubscription.Status.EXPIRED)
                | Q(
                    status=MembershipSubscription.Status.ACTIVE,
                    end_date__lt=today,
                )
            )
            .count(),
            "suspended_at": gym.suspended_at,
            "suspended_by": serialize_user(gym.suspended_by),
            "suspension_reason": gym.suspension_reason,
        }
    )
    return PlatformGymDetailSerializer(data).data


def parse_bool(value):
    lowered = value.strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    return None


class PlatformSummaryView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(operation_id="platform_summary", responses=PlatformSummarySerializer)
    def get(self, request):
        today = timezone.localdate()
        active = active_memberships()
        data = {
            "gyms_total": Gym.objects.count(),
            "gyms_active": Gym.objects.filter(is_active=True).count(),
            "gyms_suspended": Gym.objects.filter(is_active=False).count(),
            "members_total": User.objects.filter(role=User.Role.MEMBER, is_active=True).count(),
            "trainers_total": User.objects.filter(role=User.Role.TRAINER, is_active=True).count(),
            "active_memberships": active.count(),
            "memberships_expiring_7_days": active.filter(
                end_date__lte=today + timedelta(days=EXPIRING_WINDOW_DAYS)
            ).count(),
        }
        return Response(PlatformSummarySerializer(data).data)


class PlatformGymPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class PlatformGymListView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    @extend_schema(
        operation_id="platform_gym_list",
        parameters=[
            OpenApiParameter(
                "search",
                str,
                OpenApiParameter.QUERY,
                required=False,
                description="Case-insensitive gym name match.",
            ),
            OpenApiParameter(
                "is_active",
                bool,
                OpenApiParameter.QUERY,
                required=False,
                description="Filter by suspension state.",
            ),
            OpenApiParameter(
                "plan_tier",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=Gym.PlanTier.values,
            ),
            OpenApiParameter("page", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter(
                "page_size",
                int,
                OpenApiParameter.QUERY,
                required=False,
                description="Page size, default 20, maximum 100.",
            ),
        ],
        responses=PaginatedPlatformGymListSerializer,
    )
    def get(self, request):
        queryset = Gym.objects.all()

        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)

        is_active = request.query_params.get("is_active")
        if is_active is not None:
            parsed = parse_bool(is_active)
            if parsed is None:
                return Response(
                    {"is_active": ["Use true or false."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(is_active=parsed)

        plan_tier = request.query_params.get("plan_tier")
        if plan_tier is not None:
            if plan_tier not in Gym.PlanTier.values:
                return Response(
                    {"plan_tier": [f"Use one of: {', '.join(Gym.PlanTier.values)}."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(plan_tier=plan_tier)

        queryset = queryset.order_by("-created_at", "-id")
        paginator = PlatformGymPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        gym_ids = [gym.pk for gym in page]
        annotated = {gym.pk: gym for gym in with_user_counts(Gym.objects.filter(pk__in=gym_ids))}
        owners = owners_for(gym_ids)
        results = [serialize_gym(annotated[gym_id], owners.get(gym_id)) for gym_id in gym_ids]
        return paginator.get_paginated_response(results)


class PlatformGymDetailView(APIView):
    permission_classes = PLATFORM_PERMISSIONS

    def get_object(self, pk):
        gym = Gym.objects.select_related("suspended_by").filter(pk=pk).first()
        if gym is None:
            raise Http404
        return gym

    def detail_response(self, gym):
        annotated = with_user_counts(Gym.objects.filter(pk=gym.pk)).get()
        owner = owners_for([gym.pk]).get(gym.pk)
        return Response(serialize_gym(annotated, owner, detail=True))

    @extend_schema(operation_id="platform_gym_detail", responses=PlatformGymDetailSerializer)
    def get(self, request, pk):
        return self.detail_response(self.get_object(pk))

    @extend_schema(
        operation_id="platform_gym_update",
        request=PlatformGymUpdateSerializer,
        responses=PlatformGymDetailSerializer,
    )
    def patch(self, request, pk):
        gym = self.get_object(pk)
        serializer = PlatformGymUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data.get("is_active") is False:
            gym.suspend(by=request.user, reason=data.get("suspension_reason", ""))
        elif data.get("is_active") is True and not gym.is_active:
            gym.reactivate()

        plan_tier = data.get("plan_tier")
        if plan_tier is not None and plan_tier != gym.plan_tier:
            gym.plan_tier = plan_tier
            gym.save(update_fields=["plan_tier"])

        return self.detail_response(self.get_object(pk))
