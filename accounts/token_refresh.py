from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.authentication import ensure_gym_active
from accounts.models import User


class GymAwareTokenRefreshSerializer(TokenRefreshSerializer):
    """Refuse to rotate a refresh token when the user's gym is suspended."""

    def validate(self, attrs):
        token = RefreshToken(attrs["refresh"])
        user = User.objects.select_related("gym").filter(pk=token["user_id"]).first()
        if user is not None:
            ensure_gym_active(user)
        return super().validate(attrs)
