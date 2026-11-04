import pytest
from rest_framework.test import APIClient

from accounts.models import User
from gyms.models import Gym

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Passw0rd!42"
GYM_INACTIVE = "This gym is inactive."


def make_user(phone, role, gym=None):
    return User.objects.create_user(phone=phone, password=PASSWORD, role=role, gym=gym)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def login(phone, password=PASSWORD):
    return APIClient().post("/api/token/", {"phone": phone, "password": password}, format="json")


def tokens_for(phone):
    response = login(phone)
    assert response.status_code == 200, response.data
    return response.data


@pytest.fixture
def suspend():
    def _suspend(gym, by, reason="Unpaid invoice"):
        gym.suspend(by=by, reason=reason)

    return _suspend


@pytest.fixture
def super_admin():
    return make_user("+2347500000001", User.Role.SUPER_ADMIN)


def test_every_gym_role_can_log_in_before_suspension():
    gym = Gym.objects.create(name="Active Gym")
    people = [
        make_user("+2347500000101", User.Role.OWNER, gym),
        make_user("+2347500000102", User.Role.ADMIN, gym),
        make_user("+2347500000103", User.Role.TRAINER, gym),
        make_user("+2347500000104", User.Role.MEMBER, gym),
    ]

    for person in people:
        assert login(person.phone).status_code == 200


def test_suspension_blocks_login_for_every_gym_role(super_admin, suspend):
    gym = Gym.objects.create(name="Blocked Gym")
    people = [
        make_user("+2347500000201", User.Role.OWNER, gym),
        make_user("+2347500000202", User.Role.ADMIN, gym),
        make_user("+2347500000203", User.Role.TRAINER, gym),
        make_user("+2347500000204", User.Role.MEMBER, gym),
    ]
    suspend(gym, super_admin)

    for person in people:
        response = login(person.phone)
        assert response.status_code == 403, person.role
        assert response.data == {"detail": GYM_INACTIVE}


def test_wrong_password_still_returns_401_while_suspended(super_admin, suspend):
    gym = Gym.objects.create(name="Suspended Gym")
    owner = make_user("+2347500000301", User.Role.OWNER, gym)
    suspend(gym, super_admin)

    response = login(owner.phone, "not-the-password")

    assert response.status_code == 401
    assert GYM_INACTIVE not in str(response.data)


def test_suspension_blocks_refresh_tokens(super_admin, suspend):
    gym = Gym.objects.create(name="Refresh Gym")
    owner = make_user("+2347500000401", User.Role.OWNER, gym)
    tokens = tokens_for(owner.phone)
    suspend(gym, super_admin)

    response = APIClient().post(
        "/api/token/refresh/", {"refresh": tokens["refresh"]}, format="json"
    )

    assert response.status_code == 403
    assert response.data == {"detail": GYM_INACTIVE}


def test_access_token_issued_before_suspension_is_rejected(super_admin, suspend):
    gym = Gym.objects.create(name="Token Gym")
    owner = make_user("+2347500000501", User.Role.OWNER, gym)
    tokens = tokens_for(owner.phone)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert client.get("/api/me/").status_code == 200

    suspend(gym, super_admin)
    response = client.get("/api/me/")

    assert response.status_code == 403
    assert response.data == {"detail": GYM_INACTIVE}


def test_reactivation_restores_access_without_a_new_password(super_admin, suspend):
    gym = Gym.objects.create(name="Reactivated Gym")
    owner = make_user("+2347500000601", User.Role.OWNER, gym)
    tokens = tokens_for(owner.phone)
    suspend(gym, super_admin)
    assert login(owner.phone).status_code == 403

    gym.reactivate()

    assert login(owner.phone).status_code == 200
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert client.get("/api/me/").status_code == 200


def test_super_admin_is_exempt_and_can_suspend_other_gyms(super_admin):
    suspended_gym = Gym.objects.create(name="Other Gym", is_active=False)
    owner = make_user("+2347500000701", User.Role.OWNER, suspended_gym)
    assert login(owner.phone).status_code == 403

    tokens = tokens_for(super_admin.phone)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    assert client.get("/api/me/").status_code == 200
    assert client.get("/api/platform/summary/").status_code == 200
    assert client.get(f"/api/platform/gyms/{suspended_gym.pk}/").status_code == 200


def test_user_without_a_gym_and_without_platform_role_is_rejected():
    orphan = make_user("+2347500000801", User.Role.OWNER, None)

    response = login(orphan.phone)

    assert response.status_code == 403
    assert response.data == {"detail": GYM_INACTIVE}
