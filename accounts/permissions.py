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


def require_staff_permission(code):
    """Return a DRF permission class that requires one staff permission code.

    Owners always pass. Admins pass only when they hold ``code``; every other
    role is denied. This is a factory because DRF instantiates permission
    classes without arguments, so a class that takes ``code`` in ``__init__``
    can never be used inside ``permission_classes``.

    Usage::

        permission_classes = [IsAuthenticated, require_staff_permission("manage_members")]
    """

    class StaffPermissionRequired(BasePermission):
        message = "You do not have permission to perform this action."

        def has_permission(self, request, view):
            user = request.user
            if not user or not user.is_authenticated:
                return False
            if user.role == "owner":
                return True
            if user.role != "admin":
                return False
            return user.staff_permissions.filter(code=code).exists()

    StaffPermissionRequired.__name__ = f"RequireStaffPermission_{code}"
    StaffPermissionRequired.__qualname__ = StaffPermissionRequired.__name__
    return StaffPermissionRequired
