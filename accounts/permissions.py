from rest_framework.permissions import BasePermission


class IsOwner(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "owner")


class IsGymStaff(BasePermission):
    STAFF_ROLES = {"owner", "admin", "trainer"}

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.role in self.STAFF_ROLES
        )


class HasStaffPermission(BasePermission):
    def __init__(self, code=None):
        self.code = code

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role == "owner":
            return True
        code = self.code or getattr(view, "staff_permission_code", None)
        return bool(code and user.staff_permissions.filter(code=code).exists())
