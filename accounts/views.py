import hashlib
import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import Invite, User
from accounts.serializers import (
    InviteCreatedSerializer,
    InviteCreateSerializer,
    MeSerializer,
    PhoneTokenSerializer,
    RedeemInviteResponseSerializer,
    RedeemInviteSerializer,
    RegisteredGymSerializer,
    RegisterGymSerializer,
    TokenPairSerializer,
    TokenRefreshResponseSerializer,
)
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
    @extend_schema(responses=TokenRefreshResponseSerializer)
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
