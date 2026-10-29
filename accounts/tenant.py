from django.db import models
from rest_framework import viewsets


class GymScopedQuerySet(models.QuerySet):
    def for_gym(self, gym):
        gym_id = getattr(gym, "pk", gym)
        if gym_id is None:
            return self.none()
        return self.filter(gym_id=gym_id)


class GymScopedManager(models.Manager.from_queryset(GymScopedQuerySet)):
    pass


class GymOwnedModel(models.Model):
    gym = models.ForeignKey("gyms.Gym", on_delete=models.CASCADE)
    objects = GymScopedManager()

    class Meta:
        abstract = True


class GymScopedQuerysetMixin:
    """Restrict tenant-owned API querysets to the authenticated user's gym."""

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated or user.gym_id is None:
            return queryset.none()
        return queryset.filter(gym_id=user.gym_id)


class GymScopedModelViewSet(GymScopedQuerysetMixin, viewsets.ModelViewSet):
    """Base viewset for tenant-owned models; subclasses inherit gym filtering."""
