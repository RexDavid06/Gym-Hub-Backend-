import pytest
from rest_framework.test import APIClient

from accounts.models import User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym):
    return User.objects.create_user(phone=phone, password="password123", role=role, gym=gym)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_owner_can_list_admins_set_and_revoke_allowed_codes():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2348111000001", User.Role.OWNER, gym)
    admin = make_user("+2348111000002", User.Role.ADMIN, gym)
    client = client_for(owner)

    assert [row["id"] for row in client.get("/api/admins/").data] == [admin.pk]
    set_response = client.put(
        f"/api/admins/{admin.pk}/permissions/",
        {"codes": ["manage_members", "view_reports"]},
        format="json",
    )
    assert set_response.status_code == 200
    assert set(admin.staff_permissions.values_list("code", flat=True)) == {
        "manage_members",
        "view_reports",
    }
    revoke = client.delete(f"/api/admins/{admin.pk}/permissions/manage_members/")
    assert revoke.status_code == 204
    assert list(admin.staff_permissions.values_list("code", flat=True)) == ["view_reports"]


def test_admin_without_user_permission_is_blocked_and_invalid_code_rejected():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2348111000011", User.Role.OWNER, gym)
    admin = make_user("+2348111000012", User.Role.ADMIN, gym)

    assert client_for(admin).get("/api/users/").status_code == 403
    invalid = client_for(owner).put(
        f"/api/admins/{admin.pk}/permissions/",
        {"codes": ["delete_everything"]},
        format="json",
    )
    assert invalid.status_code == 400
    assert not admin.staff_permissions.exists()


def test_owner_cannot_be_restricted_or_deactivated():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2348111000021", User.Role.OWNER, gym)
    other_owner = make_user("+2348111000022", User.Role.OWNER, gym)
    client = client_for(owner)

    set_permissions = client.put(
        f"/api/admins/{other_owner.pk}/permissions/", {"codes": []}, format="json"
    )
    deactivate = client.post(f"/api/users/{other_owner.pk}/deactivate/")

    assert set_permissions.status_code == 404
    assert deactivate.status_code == 404
    assert other_owner.is_active


def test_users_list_is_gym_scoped_and_filterable():
    gym_a = Gym.objects.create(name="Gym A")
    gym_b = Gym.objects.create(name="Gym B")
    owner_a = make_user("+2348111000031", User.Role.OWNER, gym_a)
    member_a = make_user("+2348111000032", User.Role.MEMBER, gym_a)
    make_user("+2348111000033", User.Role.ADMIN, gym_a)
    member_b = make_user("+2348111000034", User.Role.MEMBER, gym_b)

    response = client_for(owner_a).get("/api/users/?role=member")

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [member_a.pk]
    assert member_b.pk not in [row["id"] for row in response.data]


def test_deactivated_user_cannot_log_in():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2348111000041", User.Role.OWNER, gym)
    member = make_user("+2348111000042", User.Role.MEMBER, gym)

    response = client_for(owner).post(f"/api/users/{member.pk}/deactivate/")
    login = APIClient().post(
        "/api/token/", {"phone": member.phone, "password": "password123"}, format="json"
    )

    assert response.status_code == 200
    assert login.status_code == 401
