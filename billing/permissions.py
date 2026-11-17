from rest_framework.permissions import BasePermission


class IsOwnerOfGym(BasePermission):
    """Only a gym owner may manage their own subscription and billing."""

    message = "Only a gym owner can access this resource."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "owner"
            and request.user.gym_id
        )
