from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import TrainerAssignment, User
from accounts.tenant import GymScopedModelViewSet
from workouts.models import WorkoutPlan
from workouts.serializers import WorkoutPlanSerializer


class WorkoutPlanViewSet(GymScopedModelViewSet):
    queryset = WorkoutPlan.objects.all()
    serializer_class = WorkoutPlanSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = (
            super().get_queryset().select_related("member", "trainer").prefetch_related("exercises")
        )
        user = self.request.user
        if user.role == User.Role.OWNER:
            pass
        elif (
            user.role == User.Role.ADMIN
            and user.staff_permissions.filter(code="view_reports").exists()
        ):
            pass
        elif user.role == User.Role.TRAINER:
            queryset = queryset.filter(trainer=user, member__trainer_assignment__trainer=user)
        elif user.role == User.Role.MEMBER:
            queryset = queryset.filter(member=user)
        else:
            return queryset.none()
        member_id = self.request.query_params.get("member")
        if member_id:
            try:
                member_id = int(member_id)
            except ValueError:
                return queryset.none()
            queryset = queryset.filter(member_id=member_id)
        return queryset

    def assigned_member_or_error(self, request):
        member_id = request.data.get("member_id")
        if member_id in (None, ""):
            return None, Response(
                {"member_id": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            member_id = int(member_id)
        except (TypeError, ValueError):
            return None, Response(
                {"member_id": ["A valid integer is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        member = get_object_or_404(
            User.objects.filter(gym_id=request.user.gym_id, role=User.Role.MEMBER, is_active=True),
            pk=member_id,
        )
        if not TrainerAssignment.objects.filter(
            gym_id=request.user.gym_id, member=member, trainer=request.user
        ).exists():
            return None, Response(
                {"detail": "You can only create workout plans for members assigned to you."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return member, None

    @extend_schema(
        parameters=[
            OpenApiParameter("member", int, OpenApiParameter.QUERY, required=False),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(request=WorkoutPlanSerializer, responses=WorkoutPlanSerializer)
    def create(self, request, *args, **kwargs):
        if request.user.role != User.Role.TRAINER:
            return Response(
                {"detail": "Only trainers can create workout plans."},
                status=status.HTTP_403_FORBIDDEN,
            )
        member, error = self.assigned_member_or_error(request)
        if error:
            return error
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop("member_id", None)
        plan = serializer.save(gym=request.user.gym, member=member, trainer=request.user)
        return Response(
            WorkoutPlanSerializer(plan).data,
            status=status.HTTP_201_CREATED,
            headers=self.get_success_headers(serializer.data),
        )

    @extend_schema(request=WorkoutPlanSerializer, responses=WorkoutPlanSerializer)
    def update(self, request, *args, **kwargs):
        if request.user.role != User.Role.TRAINER:
            return Response(
                {"detail": "Only the assigned trainer can update a workout plan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        plan = self.get_object()
        member_id = request.data.get("member_id")
        if member_id not in (None, ""):
            try:
                member_id = int(member_id)
            except (TypeError, ValueError):
                return Response(
                    {"member_id": ["A valid integer is required."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if member_id != plan.member_id:
                return Response(
                    {"member_id": ["The member cannot be changed after a plan is created."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        partial = kwargs.pop("partial", False)
        serializer = self.get_serializer(plan, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop("member_id", None)
        serializer.save()
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if request.user.role != User.Role.TRAINER:
            return Response(
                {"detail": "Only the assigned trainer can delete a workout plan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)
