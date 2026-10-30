import inspect

from django.urls import get_resolver
from rest_framework.viewsets import ViewSetMixin

from accounts.tenant import GymScopedQuerysetMixin
from accounts.views import UserListViewSet
from gyms.models import Branch
from memberships.views import (
    MembershipPaymentViewSet,
    MembershipPlanViewSet,
    MembershipSubscriptionViewSet,
)
from workouts.views import WorkoutPlanViewSet


def walk_patterns(patterns):
    for entry in patterns:
        if hasattr(entry, "url_patterns"):
            yield from walk_patterns(entry.url_patterns)
        else:
            viewset = getattr(entry.callback, "cls", None)
            if viewset is not None and issubclass(viewset, ViewSetMixin):
                yield viewset


def registered_viewsets():
    return set(walk_patterns(get_resolver().url_patterns))


def model_is_tenant_owned(model):
    return "gym" in {field.name for field in model._meta.get_fields()}


def scopes_by_gym(viewset):
    """A tenant ViewSet is safe only if it scopes through the mixin or get_queryset."""
    if GymScopedQuerysetMixin in viewset.__mro__:
        return True
    get_queryset = getattr(viewset, "get_queryset", None)
    if get_queryset is None:
        return False
    return "gym_id" in inspect.getsource(get_queryset)


def tenant_viewsets():
    for viewset in registered_viewsets():
        queryset = getattr(viewset, "queryset", None)
        if queryset is None:
            continue
        if model_is_tenant_owned(queryset.model):
            yield viewset


def test_tenancy_guard_discovers_every_tenant_viewset():
    found = set(tenant_viewsets())

    assert {
        UserListViewSet,
        WorkoutPlanViewSet,
        MembershipPlanViewSet,
        MembershipSubscriptionViewSet,
        MembershipPaymentViewSet,
    } <= found


def test_every_tenant_viewset_scopes_querysets_by_request_gym():
    offenders = sorted(
        viewset.__name__ for viewset in tenant_viewsets() if not scopes_by_gym(viewset)
    )

    assert offenders == []


def test_tenancy_guard_rejects_a_viewset_that_does_not_scope():
    class LeakyBranchViewSet(ViewSetMixin):
        queryset = Branch.objects.all()

    class ScopedBranchViewSet(GymScopedQuerysetMixin, ViewSetMixin):
        queryset = Branch.objects.all()

    assert scopes_by_gym(LeakyBranchViewSet) is False
    assert scopes_by_gym(ScopedBranchViewSet) is True
