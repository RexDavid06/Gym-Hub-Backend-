import hashlib
import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import Invite, StaffPermission, TrainerAssignment, User
from accounts.permissions import IsOwner
from accounts.serializers import (
    InviteCreatedSerializer,
    InviteCreateSerializer,
    InviteListSerializer,
    MemberTrainerSerializer,
    MeSerializer,
    PhoneTokenSerializer,
    RedeemInviteResponseSerializer,
    RedeemInviteSerializer,
    RegisteredGymSerializer,
    RegisterGymSerializer,
    StaffPermissionSetSerializer,
    TokenPairSerializer,
    TokenRefreshRequestSerializer,
    TokenRefreshResponseSerializer,
    TrainerAssignmentInputSerializer,
    UserListSerializer,
    UserSummarySerializer,
)
from accounts.tenant import GymScopedModelViewSet
from gyms.models import Gym


class PhoneTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "phone"

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["gym_id"] = user.gym_id
        return token


class PhoneTokenObtainPairView(TokenObtainPairView):
    serializer_class = PhoneTokenObtainPairSerializer
    permission_classes = [AllowAny]

    @extend_schema(request=PhoneTokenSerializer, responses=TokenPairSerializer)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class PhoneTokenRefreshView(TokenRefreshView):
    @extend_schema(request=TokenRefreshRequestSerializer, responses=TokenRefreshResponseSerializer)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user).data, status=status.HTTP_200_OK)


class RegisterGymView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=RegisterGymSerializer, responses=RegisteredGymSerializer)
    def post(self, request):
        serializer = RegisterGymSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                gym = Gym.objects.create(name=data["gym_name"])
                user = User.objects.create_user(
                    phone=data["phone"],
                    password=data["password"],
                    name=data["name"],
                    gym=gym,
                    role=User.Role.OWNER,
                )
        except IntegrityError:
            return Response(
                {"phone": ["A user with this phone number already exists."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"gym_id": gym.pk, "user_id": user.pk, "role": user.role},
            status=status.HTTP_201_CREATED,
        )


class InviteView(APIView):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated(), IsOwner()]
        return [IsAuthenticated()]

    @extend_schema(responses=InviteListSerializer(many=True))
    def get(self, request):
        invites = Invite.objects.filter(gym_id=request.user.gym_id).order_by("-created_at")
        return Response(InviteListSerializer(invites, many=True).data)

    @extend_schema(request=InviteCreateSerializer, responses=InviteCreatedSerializer)
    def post(self, request):
        serializer = InviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_role = serializer.validated_data["role"]
        inviter = request.user

        allowed = False
        if inviter.role == User.Role.OWNER:
            allowed = True
        elif inviter.role == User.Role.ADMIN:
            permission_by_role = {
                Invite.Role.TRAINER: "manage_trainers",
                Invite.Role.MEMBER: "manage_members",
            }
            permission_code = permission_by_role.get(target_role)
            allowed = bool(
                permission_code and inviter.staff_permissions.filter(code=permission_code).exists()
            )
        if not allowed:
            return Response({"detail": "You may not invite this role."}, status=403)
        if inviter.gym_id is None:
            return Response({"detail": "You may not invite this role."}, status=403)

        raw_code = secrets.token_urlsafe(32)
        invite = Invite.objects.create(
            gym=inviter.gym,
            role=target_role,
            phone=serializer.validated_data["phone"],
            name=serializer.validated_data["name"],
            created_by=inviter,
            token_hash=hashlib.sha256(raw_code.encode()).hexdigest(),
        )
        response_data = InviteCreatedSerializer(
            {
                "id": invite.pk,
                "role": invite.role,
                "phone": invite.phone,
                "name": invite.name,
                "expires_at": invite.expires_at,
                "code": raw_code,
            }
        ).data
        return Response(response_data, status=status.HTTP_201_CREATED)


class RedeemInviteView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invite_redeem"

    @extend_schema(request=RedeemInviteSerializer, responses=RedeemInviteResponseSerializer)
    def post(self, request):
        serializer = RedeemInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code_hash = hashlib.sha256(serializer.validated_data["code"].encode()).hexdigest()
        generic_error = {"detail": "Invite code is invalid or unavailable."}
        try:
            with transaction.atomic():
                invite = Invite.objects.select_for_update().get(token_hash=code_hash)
                if invite.used_at is not None or invite.expires_at <= timezone.now():
                    return Response(generic_error, status=status.HTTP_400_BAD_REQUEST)
                user = User.objects.create_user(
                    phone=invite.phone,
                    password=serializer.validated_data["password"],
                    name=invite.name,
                    gym=invite.gym,
                    role=invite.role,
                )
                invite.used_at = timezone.now()
                invite.save(update_fields=["used_at"])
        except (Invite.DoesNotExist, IntegrityError):
            return Response(generic_error, status=status.HTTP_400_BAD_REQUEST)

        refresh = PhoneTokenObtainPairSerializer.get_token(user)
        return Response(
            {"refresh": str(refresh), "access": str(refresh.access_token)},
            status=status.HTTP_200_OK,
        )


class AdminListView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    @extend_schema(responses=UserListSerializer(many=True))
    def get(self, request):
        admins = User.objects.filter(gym_id=request.user.gym_id, role=User.Role.ADMIN)
        return Response(UserListSerializer(admins, many=True).data)


def get_gym_admin_or_404(owner, user_id):
    from django.shortcuts import get_object_or_404

    return get_object_or_404(
        User.objects.filter(gym_id=owner.gym_id, role=User.Role.ADMIN), pk=user_id
    )


class AdminPermissionsView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]
    http_method_names = ["put", "options"]

    @extend_schema(
        operation_id="admin_permissions_set",
        request=StaffPermissionSetSerializer,
        responses=UserListSerializer,
    )
    def put(self, request, user_id):
        admin = get_gym_admin_or_404(request.user, user_id)
        serializer = StaffPermissionSetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        StaffPermission.objects.filter(user=admin).delete()
        StaffPermission.objects.bulk_create(
            [
                StaffPermission(user=admin, code=code)
                for code in set(serializer.validated_data["codes"])
            ]
        )
        return Response(UserListSerializer(admin).data)

    @extend_schema(operation_id="admin_permissions_revoke", request=None, responses=None)
    def delete(self, request, user_id, code):
        admin = get_gym_admin_or_404(request.user, user_id)
        StaffPermission.objects.filter(user=admin, code=code).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminPermissionRevokeView(AdminPermissionsView):
    http_method_names = ["delete", "options"]


def has_any_staff_permission(user, codes):
    if user.role == User.Role.OWNER:
        return True
    return user.role == User.Role.ADMIN and user.staff_permissions.filter(code__in=codes).exists()


def can_assign_trainers(user):
    return has_any_staff_permission(user, ["assign_trainers"])


class TrainerListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UserSummarySerializer(many=True))
    def get(self, request):
        user = request.user
        if not has_any_staff_permission(user, ["manage_trainers", "assign_trainers"]):
            return Response({"detail": "You may not view trainers."}, status=403)
        trainers = User.objects.filter(
            gym_id=user.gym_id, role=User.Role.TRAINER, is_active=True
        ).order_by("name", "phone")
        return Response(UserSummarySerializer(trainers, many=True).data)


class MemberTrainerView(APIView):
    permission_classes = [IsAuthenticated]

    def get_member(self, request, user_id):
        from django.shortcuts import get_object_or_404

        return get_object_or_404(
            User.objects.filter(gym_id=request.user.gym_id, role=User.Role.MEMBER), pk=user_id
        )

    def assignment_response(self, member):
        assignment = getattr(member, "trainer_assignment", None)
        return MemberTrainerSerializer(
            {"member": member, "trainer": assignment.trainer if assignment else None}
        ).data

    @extend_schema(responses=MemberTrainerSerializer)
    def get(self, request, user_id):
        member = self.get_member(request, user_id)
        assignment = getattr(member, "trainer_assignment", None)
        user = request.user
        allowed = (
            user.role == User.Role.OWNER
            or has_any_staff_permission(user, ["assign_trainers", "manage_members"])
            or (user.role == User.Role.TRAINER and assignment and assignment.trainer_id == user.id)
            or (user.role == User.Role.MEMBER and member.id == user.id)
        )
        if not allowed:
            return Response({"detail": "Member not found."}, status=404)
        return Response(self.assignment_response(member))

    @extend_schema(request=TrainerAssignmentInputSerializer, responses=MemberTrainerSerializer)
    def put(self, request, user_id):
        if not can_assign_trainers(request.user):
            return Response({"detail": "You may not assign trainers."}, status=403)
        member = self.get_member(request, user_id)
        serializer = TrainerAssignmentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trainer_id = serializer.validated_data["trainer_id"]
        if trainer_id is None:
            TrainerAssignment.objects.filter(member=member).delete()
        else:
            from django.shortcuts import get_object_or_404

            trainer = get_object_or_404(
                User.objects.filter(
                    gym_id=request.user.gym_id,
                    role=User.Role.TRAINER,
                    is_active=True,
                ),
                pk=trainer_id,
            )
            TrainerAssignment.objects.update_or_create(
                member=member,
                defaults={
                    "gym": request.user.gym,
                    "trainer": trainer,
                    "assigned_by": request.user,
                },
            )
        member = User.objects.select_related("trainer_assignment__trainer").get(pk=member.pk)
        return Response(self.assignment_response(member))

    @extend_schema(request=None, responses=None)
    def delete(self, request, user_id):
        if not can_assign_trainers(request.user):
            return Response({"detail": "You may not assign trainers."}, status=403)
        member = self.get_member(request, user_id)
        TrainerAssignment.objects.filter(member=member).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyMembersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UserListSerializer(many=True))
    def get(self, request):
        if request.user.role != User.Role.TRAINER:
            return Response({"detail": "Only trainers can view their members."}, status=403)
        members = (
            User.objects.filter(
                gym_id=request.user.gym_id,
                role=User.Role.MEMBER,
                is_active=True,
                trainer_assignment__trainer=request.user,
                trainer_assignment__gym_id=request.user.gym_id,
            )
            .select_related("trainer_assignment__trainer")
            .order_by("name", "phone")
        )
        return Response(UserListSerializer(members, many=True).data)


class MyTrainerView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MemberTrainerSerializer)
    def get(self, request):
        if request.user.role != User.Role.MEMBER:
            return Response({"detail": "Only members have an assigned trainer."}, status=403)
        return Response(MemberTrainerView().assignment_response(request.user))


class DeactivateUserView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    @extend_schema(request=None, responses=UserListSerializer)
    def post(self, request, user_id):
        from django.shortcuts import get_object_or_404

        user = get_object_or_404(
            User.objects.filter(gym_id=request.user.gym_id).exclude(role=User.Role.OWNER),
            pk=user_id,
        )
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(UserListSerializer(user).data)


class UserListViewSet(GymScopedModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset().select_related("trainer_assignment__trainer")
        user = self.request.user
        if user.role == User.Role.OWNER:
            pass
        elif user.role == User.Role.ADMIN:
            codes = set(user.staff_permissions.values_list("code", flat=True))
            roles = []
            if "manage_members" in codes:
                roles.append(User.Role.MEMBER)
            if "manage_trainers" in codes:
                roles.append(User.Role.TRAINER)
            if not roles:
                return queryset.none()
            queryset = queryset.filter(role__in=roles)
        else:
            return queryset.none()
        role = self.request.query_params.get("role")
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "role",
                str,
                OpenApiParameter.QUERY,
                required=False,
                enum=User.Role.values,
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        if (
            request.user.role != User.Role.OWNER
            and not request.user.staff_permissions.filter(
                code__in=["manage_members", "manage_trainers"]
            ).exists()
        ):
            return Response({"detail": "You do not have permission to list users."}, status=403)
        return super().list(request, *args, **kwargs)
