import inspect

from django.urls import get_resolver
from rest_framework.viewsets import ViewSetMixin

from accounts.tenant import GymScopedQuerysetMixin
from accounts.views import UserListViewSet
from billing.models import (
    BillingPayment,
    BillingPlan,
    BillingPlanPrice,
    DunningAttempt,
    GymBillingSubscription,
    Invoice,
    WebhookEvent,
)
from gyms.models import Branch
from gyms.views import BranchViewSet
from memberships.views import (
    MembershipPaymentViewSet,
    MembershipPlanViewSet,
    MembershipSubscriptionViewSet,
)
from notifications.views import NotificationViewSet
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


# Platform-owned billing rows reference a gym, but they belong to the platform.
# They are reachable only through /api/platform/ (super admin) and the owner's
# own gym-scoped /api/billing/ endpoints. They must never be tenant-scoped by
# GymScopedQuerysetMixin, so the guard treats them as an explicit exemption.
PLATFORM_OWNED_MODELS = {
    BillingPlan,
    BillingPlanPrice,
    GymBillingSubscription,
    Invoice,
    BillingPayment,
    WebhookEvent,
    DunningAttempt,
}


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
        if queryset.model in PLATFORM_OWNED_MODELS:
            continue
        if model_is_tenant_owned(queryset.model):
            yield viewset


def test_tenancy_guard_discovers_every_tenant_viewset():
    found = set(tenant_viewsets())

    assert {
        UserListViewSet,
        BranchViewSet,
        WorkoutPlanViewSet,
        MembershipPlanViewSet,
        MembershipSubscriptionViewSet,
        MembershipPaymentViewSet,
        NotificationViewSet,
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


def test_notification_viewset_is_gym_scoped():
    assert scopes_by_gym(NotificationViewSet) is True


def test_billing_models_are_an_intentional_platform_exemption():
    """Billing rows have a gym FK but are platform-owned, not tenant-owned."""
    gym_bearing = {
        GymBillingSubscription,
        Invoice,
        BillingPayment,
    }
    assert gym_bearing <= PLATFORM_OWNED_MODELS
    # The guard would otherwise treat these as tenant models.
    assert all(model_is_tenant_owned(model) for model in gym_bearing)
    guarded = {viewset.queryset.model for viewset in tenant_viewsets()}
    assert guarded.isdisjoint(PLATFORM_OWNED_MODELS)
    # Every billing model, gym-bearing or not, is part of the exemption.
    assert {
        BillingPlan,
        BillingPlanPrice,
        GymBillingSubscription,
        Invoice,
        BillingPayment,
        WebhookEvent,
        DunningAttempt,
    } == PLATFORM_OWNED_MODELS
