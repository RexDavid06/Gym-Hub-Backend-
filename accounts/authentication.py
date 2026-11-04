from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

GYM_INACTIVE_MESSAGE = "This gym is inactive."


def ensure_gym_active(user):
    """Reject users whose gym is suspended. Platform staff are exempt.

    A user without a gym is only valid when they are platform staff; every
    other role must belong to an active gym.
    """
    # Imported lazily: this module is imported while Django is still setting
    # up the app registry, before the User model can be imported safely.
    from accounts.models import User

    if user.role == User.Role.SUPER_ADMIN:
        return
    gym = user.gym
    if gym is None or not gym.is_active:
        raise PermissionDenied(GYM_INACTIVE_MESSAGE)


class GymAwareJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        ensure_gym_active(user)
        return user
