from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated

from accounts.permissions import IsOwner
from accounts.tenant import GymScopedModelViewSet
from gyms.models import Branch
from gyms.serializers import BranchSerializer


class CanViewBranches(BasePermission):
    """Owners and admins need the branch list to assign people to branches."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in {"owner", "admin"}
        )


class BranchViewSet(GymScopedModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated(), CanViewBranches()]
        return [IsAuthenticated(), IsOwner()]

    def get_queryset(self):
        return super().get_queryset().order_by("name", "id")

    def perform_create(self, serializer):
        serializer.save(gym=self.request.user.gym)
