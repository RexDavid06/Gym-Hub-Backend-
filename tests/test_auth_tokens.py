import pytest
from rest_framework.test import APIClient

from accounts.models import User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


def make_user(phone, role=User.Role.OWNER):
    gym = Gym.objects.create(name=f"Gym {phone}")
    return User.objects.create_user(phone=phone, password="password123", gym=gym, role=role)


def login(user):
    response = APIClient().post(
        "/api/token/", {"phone": user.phone, "password": "password123"}, format="json"
    )
    assert response.status_code == 200
    return response.data


def authed(access):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def refresh(token):
    return APIClient().post("/api/token/refresh/", {"refresh": token}, format="json")


def test_logout_blacklists_the_refresh_token():
    tokens = login(make_user("+2347100000001"))

    response = authed(tokens["access"]).post(
        "/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json"
    )

    assert response.status_code == 204
    assert refresh(tokens["refresh"]).status_code == 401


def test_logout_requires_authentication():
    tokens = login(make_user("+2347100000002"))

    response = APIClient().post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")

    assert response.status_code == 401
    assert refresh(tokens["refresh"]).status_code == 200


def test_logout_rejects_another_users_refresh_token():
    owner_tokens = login(make_user("+2347100000003"))
    other_tokens = login(make_user("+2347100000004"))

    response = authed(owner_tokens["access"]).post(
        "/api/auth/logout/", {"refresh": other_tokens["refresh"]}, format="json"
    )

    assert response.status_code == 400
    assert refresh(other_tokens["refresh"]).status_code == 200


def test_logout_rejects_a_garbage_refresh_token():
    tokens = login(make_user("+2347100000005"))

    response = authed(tokens["access"]).post(
        "/api/auth/logout/", {"refresh": "not-a-token"}, format="json"
    )

    assert response.status_code == 400


def test_refresh_rotation_returns_a_new_refresh_token():
    tokens = login(make_user("+2347100000006"))

    rotated = refresh(tokens["refresh"])

    assert rotated.status_code == 200
    assert rotated.data["access"]
    assert rotated.data["refresh"]
    assert rotated.data["refresh"] != tokens["refresh"]


def test_rotated_refresh_token_cannot_be_reused():
    tokens = login(make_user("+2347100000007"))

    rotated = refresh(tokens["refresh"])
    replay = refresh(tokens["refresh"])

    assert rotated.status_code == 200
    assert replay.status_code == 401
    assert refresh(rotated.data["refresh"]).status_code == 200
