from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import StaffPermission


class PhoneTokenSerializer(serializers.Serializer):
    phone = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class TokenPairSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()


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
