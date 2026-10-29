from types import SimpleNamespace

import pytest
from django.contrib.auth import authenticate
from django.db import connection, models
from rest_framework import serializers
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from accounts.models import StaffPermission, User
from accounts.permissions import HasStaffPermission, IsGymStaff, IsOwner
from accounts.tenant import GymOwnedModel, GymScopedManager, GymScopedModelViewSet
from gyms.models import Branch, Gym

pytestmark = pytest.mark.django_db


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "gym", "name", "address"]


class BranchViewSet(GymScopedModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer


def test_user_creation_by_phone():
    user = User.objects.create_user(phone="+2348000000001", password="pass1234")

    assert user.phone == "+2348000000001"
    assert user.check_password("pass1234")
    assert user.USERNAME_FIELD == "phone"


def test_jwt_login_and_refresh_by_phone():
    user = User.objects.create_user(phone="+2348000000002", password="pass1234")

    assert authenticate(phone=user.phone, password="pass1234") == user
    client = APIClient()
    login_response = client.post(
        "/api/token/", {"phone": user.phone, "password": "pass1234"}, format="json"
    )
    assert login_response.status_code == 200
    assert login_response.data["access"]
    assert login_response.data["refresh"]

    refresh_response = client.post(
        "/api/token/refresh/", {"refresh": login_response.data["refresh"]}, format="json"
    )
    assert refresh_response.status_code == 200
    assert refresh_response.data["access"]


def test_me_returns_role_gym_and_permission_codes():
    gym = Gym.objects.create(name="Fit Gym")
    user = User.objects.create_user(
        phone="+2348000000003", password="pass1234", gym=gym, role="admin"
    )
    StaffPermission.objects.create(user=user, code="members.read")
    client = APIClient()
    login_response = client.post(
        "/api/token/", {"phone": user.phone, "password": "pass1234"}, format="json"
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}")

    response = client.get("/api/me/")

    assert response.status_code == 200
    assert response.data["role"] == "admin"
    assert response.data["gym"] == {"id": gym.id, "name": gym.name}
    assert response.data["permission_codes"] == ["members.read"]


@pytest.mark.django_db(transaction=True)
def test_gym_scoped_queryset_never_returns_another_gyms_rows():
    first = Gym.objects.create(name="One")
    second = Gym.objects.create(name="Two")

    class GymScopedLeakRow(GymOwnedModel):
        label = models.CharField(max_length=30)

        class Meta:
            app_label = "tests"

    with connection.schema_editor() as editor:
        editor.create_model(GymScopedLeakRow)
    try:
        GymScopedLeakRow.objects.create(gym=first, label="first")
        GymScopedLeakRow.objects.create(gym=second, label="second")

        assert isinstance(GymScopedLeakRow.objects, GymScopedManager)
        assert list(GymScopedLeakRow.objects.for_gym(first).values_list("label", flat=True)) == [
            "first"
        ]
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(GymScopedLeakRow)


@pytest.mark.django_db(transaction=True)
def test_gymless_user_gets_empty_queryset_from_mixin_and_manager():
    gym = Gym.objects.create(name="One")
    Branch.objects.create(gym=gym, name="Branch One")
    user = User.objects.create_user(phone="+2348000000004", password="pass1234", gym=None)

    class GymlessRow(GymOwnedModel):
        label = models.CharField(max_length=30)

        class Meta:
            app_label = "tests"

    with connection.schema_editor() as editor:
        editor.create_model(GymlessRow)
    try:
        GymlessRow.objects.create(gym=gym, label="tenant row")
        view = BranchViewSet()
        view.request = SimpleNamespace(user=user)

        assert list(view.get_queryset()) == []
        assert list(GymlessRow.objects.for_gym(user.gym_id)) == []
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(GymlessRow)


def test_gym_a_user_cannot_retrieve_gym_b_branch_by_id():
    gym_a = Gym.objects.create(name="Gym A")
    gym_b = Gym.objects.create(name="Gym B")
    branch_b = Branch.objects.create(gym=gym_b, name="Branch B")
    user_a = User.objects.create_user(
        phone="+2348000000005", password="pass1234", gym=gym_a, role="admin"
    )
    request = APIRequestFactory().get(f"/branches/{branch_b.pk}/")
    force_authenticate(request, user=user_a)

    response = BranchViewSet.as_view({"get": "retrieve"})(request, pk=branch_b.pk)

    assert response.status_code == 404


def test_has_staff_permission_allows_assigned_code():
    gym = Gym.objects.create(name="Fit Gym")
    user = User.objects.create_user(
        phone="+2348000000006", password="pass1234", gym=gym, role="admin"
    )
    StaffPermission.objects.create(user=user, code="members.read")
    request = SimpleNamespace(user=user)

    assert HasStaffPermission("members.read").has_permission(request, SimpleNamespace())


def test_has_staff_permission_denies_unassigned_code():
    gym = Gym.objects.create(name="Fit Gym")
    user = User.objects.create_user(
        phone="+2348000000007", password="pass1234", gym=gym, role="admin"
    )
    request = SimpleNamespace(user=user)

    assert not HasStaffPermission("members.write").has_permission(request, SimpleNamespace())


def test_owner_always_passes_permission_checks():
    gym = Gym.objects.create(name="Fit Gym")
    owner = User.objects.create_user(
        phone="+2348000000008", password="pass1234", gym=gym, role="owner"
    )
    request = SimpleNamespace(user=owner)
    view = SimpleNamespace()

    assert IsOwner().has_permission(request, view)
    assert IsGymStaff().has_permission(request, view)
    assert HasStaffPermission("unassigned.permission").has_permission(request, view)
