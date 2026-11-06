from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsOwner
from accounts.tenant import GymScopedModelViewSet
from notifications.models import Notification, NotificationPreference, NotificationSetting
from notifications.permissions import IsOwnerOrAdmin
from notifications.serializers import (
    NotificationPreferenceSerializer,
    NotificationSerializer,
    NotificationSettingSerializer,
)
from notifications.tasks import deliver_notification


class NotificationPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100


class NotificationSettingView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    @extend_schema(responses=NotificationSettingSerializer)
    def get(self, request):
        if request.user.gym_id is None:
            return Response({"detail": "No gym is assigned to this account."}, status=403)
        setting = NotificationSetting.objects.filter(gym_id=request.user.gym_id).first()
        if setting is None:
            setting = NotificationSetting(gym=request.user.gym)
        return Response(NotificationSettingSerializer(setting).data)

    @extend_schema(
        request=NotificationSettingSerializer,
        responses=NotificationSettingSerializer,
    )
    def patch(self, request):
        if request.user.gym_id is None:
            return Response({"detail": "No gym is assigned to this account."}, status=403)
        setting = NotificationSetting.objects.filter(gym_id=request.user.gym_id).first()
        serializer = NotificationSettingSerializer(
            setting or NotificationSetting(gym=request.user.gym),
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class NotificationViewSet(GymScopedModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination
    http_method_names = ["get", "head", "options", "post"]

    def get_permissions(self):
        if getattr(self, "action", None) == "retry":
            return [IsAuthenticated(), IsOwner()]
        return [IsAuthenticated(), IsOwnerOrAdmin()]

    def get_queryset(self):
        queryset = super().get_queryset().select_related("recipient")
        filters = {
            "event": Notification.Event.values,
            "status": Notification.Status.values,
            "channel": Notification.Channel.values,
            "schedule_key": Notification.ScheduleKey.values,
        }
        for parameter, allowed in filters.items():
            value = self.request.query_params.get(parameter)
            if value:
                if value not in allowed:
                    return queryset.none()
                queryset = queryset.filter(**{parameter: value})
        return queryset.order_by("-created_at", "-id")

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "event",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=Notification.Event.values,
            ),
            OpenApiParameter(
                "status",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=Notification.Status.values,
            ),
            OpenApiParameter(
                "channel",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=Notification.Channel.values,
            ),
            OpenApiParameter(
                "schedule_key",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=Notification.ScheduleKey.values,
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(request=None, responses={202: NotificationSerializer})
    def retry(self, request, pk):
        notification = self.get_object()
        if notification.status != Notification.Status.FAILED:
            return Response(
                {"detail": "Only failed notifications can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        notification.status = Notification.Status.PENDING
        notification.skip_reason = ""
        notification.last_error = ""
        notification.scheduled_at = timezone.now()
        notification.save(
            update_fields=["status", "skip_reason", "last_error", "scheduled_at", "updated_at"]
        )
        deliver_notification.delay(notification.pk)
        return Response(self.get_serializer(notification).data, status=status.HTTP_202_ACCEPTED)


class NotificationPreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def can_manage(self, user):
        if user.role == User.Role.OWNER:
            return True
        return (
            user.role == User.Role.ADMIN
            and user.staff_permissions.filter(code="manage_members").exists()
        )

    def get_member(self, request, user_id):
        member = get_object_or_404(User.objects.filter(gym_id=request.user.gym_id), pk=user_id)
        if member.role != User.Role.MEMBER:
            return None
        return member

    @extend_schema(responses=NotificationPreferenceSerializer)
    def get(self, request, user_id):
        if not self.can_manage(request.user):
            return Response({"detail": "You may not manage this preference."}, status=403)
        member = self.get_member(request, user_id)
        if member is None:
            return Response(
                {"detail": "Notification preferences apply only to members."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        preference = NotificationPreference.objects.filter(
            gym_id=request.user.gym_id, user=member
        ).first()
        return Response({"whatsapp_opt_in": bool(preference and preference.whatsapp_opt_in)})

    @extend_schema(
        request=NotificationPreferenceSerializer,
        responses=NotificationPreferenceSerializer,
    )
    def put(self, request, user_id):
        if not self.can_manage(request.user):
            return Response({"detail": "You may not manage this preference."}, status=403)
        member = self.get_member(request, user_id)
        if member is None:
            return Response(
                {"detail": "Notification preferences apply only to members."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = NotificationPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preference, _ = NotificationPreference.objects.update_or_create(
            user=member,
            defaults={
                "gym": request.user.gym,
                "whatsapp_opt_in": serializer.validated_data["whatsapp_opt_in"],
            },
        )
        return Response(
            {"whatsapp_opt_in": preference.whatsapp_opt_in},
            status=status.HTTP_200_OK,
        )
