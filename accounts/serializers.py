from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import Invite, StaffPermission, User


class PhoneTokenSerializer(serializers.Serializer):
    phone = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class TokenPairSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class TokenRefreshResponseSerializer(serializers.Serializer):
    access = serializers.CharField()


class GymSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    phone = serializers.CharField()
    role = serializers.CharField()
    gym = serializers.SerializerMethodField()
    permission_codes = serializers.SerializerMethodField()

    @extend_schema_field(GymSummarySerializer(allow_null=True))
    def get_gym(self, user):
        if not user.gym_id:
            return None
        return {"id": user.gym_id, "name": user.gym.name}

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permission_codes(self, user):
        if user.role == "owner":
            return list(StaffPermission.objects.filter(user=user).values_list("code", flat=True))
        return list(user.staff_permissions.values_list("code", flat=True))


class RegisterGymSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=32)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    gym_name = serializers.CharField(max_length=200)

    def validate_phone(self, value):
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")
        return value


class InviteCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Invite.Role.choices)
    phone = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=200)

    def validate_phone(self, value):
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("A user with this phone number already exists.")
        return value


class InviteCreatedSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField()
    phone = serializers.CharField()
    name = serializers.CharField()
    expires_at = serializers.DateTimeField()
    code = serializers.CharField()


class InviteListSerializer(serializers.ModelSerializer):
    created_by = serializers.IntegerField(source="created_by_id", read_only=True)

    class Meta:
        model = Invite
        fields = [
            "id",
            "role",
            "phone",
            "name",
            "expires_at",
            "used_at",
            "created_at",
            "created_by",
        ]


class RedeemInviteSerializer(serializers.Serializer):
    code = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class RegisteredGymSerializer(serializers.Serializer):
    gym_id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    role = serializers.CharField()


class RedeemInviteResponseSerializer(TokenPairSerializer):
    pass


class UserListSerializer(serializers.ModelSerializer):
    permission_codes = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "phone",
            "role",
            "gym",
            "is_active",
            "date_joined",
            "permission_codes",
        ]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permission_codes(self, user):
        if user.role != User.Role.ADMIN:
            return []
        return list(user.staff_permissions.values_list("code", flat=True))


ALLOWED_STAFF_PERMISSION_CODES = (
    "manage_members",
    "manage_trainers",
    "manage_payments",
    "assign_trainers",
    "view_reports",
)


class StaffPermissionSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=ALLOWED_STAFF_PERMISSION_CODES)


class StaffPermissionSetSerializer(serializers.Serializer):
    codes = serializers.ListField(
        child=serializers.ChoiceField(choices=ALLOWED_STAFF_PERMISSION_CODES), allow_empty=True
    )
