from rest_framework.permissions import BasePermission


class CanManageMemberships(BasePermission):
    message = "You do not have permission to manage memberships and payments."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role == "owner":
            return True
        return (
            user.role == "admin" and user.staff_permissions.filter(code="manage_payments").exists()
        )
