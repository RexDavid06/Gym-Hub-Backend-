import pytest
from rest_framework.test import APIClient

from accounts.models import User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


def login(ip):
    return APIClient().post(
        "/api/token/",
        {"phone": "+2347000000001", "password": "password123"},
        format="json",
        REMOTE_ADDR=ip,
    )


def test_login_is_throttled_to_ten_attempts_per_minute_per_ip():
    gym = Gym.objects.create(name="Gym")
    User.objects.create_user(
        phone="+2347000000001", password="password123", gym=gym, role=User.Role.OWNER
    )

    allowed = [login("10.0.0.1").status_code for _ in range(10)]
    blocked = login("10.0.0.1").status_code

    assert allowed == [200] * 10
    assert blocked == 429


def test_login_throttle_is_scoped_per_ip():
    gym = Gym.objects.create(name="Gym")
    User.objects.create_user(
        phone="+2347000000001", password="password123", gym=gym, role=User.Role.OWNER
    )
    for _ in range(10):
        login("10.0.0.2")

    assert login("10.0.0.2").status_code == 429
    assert login("10.0.0.3").status_code == 200


def test_register_gym_is_throttled_to_five_per_hour_per_ip():
    def register(index):
        return APIClient().post(
            "/api/auth/register-gym/",
            {
                "name": f"Owner {index}",
                "phone": f"+23470000001{index:02d}",
                "password": "password123",
                "gym_name": f"Gym {index}",
            },
            format="json",
            REMOTE_ADDR="10.0.0.4",
        )

    allowed = [register(index).status_code for index in range(5)]
    blocked = register(99).status_code

    assert allowed == [201] * 5
    assert blocked == 429
    assert Gym.objects.count() == 5


def test_register_gym_throttle_is_scoped_per_ip():
    def register(index, ip):
        return APIClient().post(
            "/api/auth/register-gym/",
            {
                "name": f"Owner {index}",
                "phone": f"+23470000002{index:02d}",
                "password": "password123",
                "gym_name": f"Gym {index}",
            },
            format="json",
            REMOTE_ADDR=ip,
        )

    for index in range(5):
        register(index, "10.0.0.5")

    assert register(50, "10.0.0.5").status_code == 429
    assert register(51, "10.0.0.6").status_code == 201
