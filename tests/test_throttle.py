from contextlib import contextmanager

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings
from rest_framework.settings import api_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


@contextmanager
def rest_framework_overrides(**overrides):
    """Swap REST_FRAMEWORK settings; Django's setting_changed signal reloads DRF."""
    with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, **overrides}):
        yield


def login(phone, ip="10.0.0.1", **extra):
    return APIClient().post(
        "/api/token/",
        {"phone": phone, "password": "password123"},
        format="json",
        REMOTE_ADDR=ip,
        **extra,
    )


def register(index, ip):
    return APIClient().post(
        "/api/auth/register-gym/",
        {
            "name": f"Owner {index}",
            "phone": f"+2347000000{index:03d}",
            "password": "password123",
            "gym_name": f"Gym {index}",
        },
        format="json",
        REMOTE_ADDR=ip,
    )


def test_login_is_throttled_to_thirty_attempts_per_minute_per_ip():
    statuses = [login(f"+23470010000{i:02d}", "10.0.0.1").status_code for i in range(30)]

    assert set(statuses) == {401}
    assert login("+2347001000099", "10.0.0.1").status_code == 429


def test_different_phones_from_one_ip_are_not_blocked_by_the_phone_limit():
    gym = Gym.objects.create(name="Gym")
    phones = [f"+23470020000{i:02d}" for i in range(10)]
    for phone in phones:
        User.objects.create_user(
            phone=phone, password="password123", gym=gym, role=User.Role.MEMBER
        )

    statuses = [login(phone, "10.0.2.5").status_code for phone in phones]

    assert statuses == [200] * 10


def test_same_phone_is_limited_to_five_attempts_per_minute():
    statuses = [login("+2347000000007", f"10.0.1.{index}").status_code for index in range(5)]

    assert set(statuses) == {401}
    assert login("+2347000000007", "10.0.1.99").status_code == 429


def test_phone_limit_is_per_phone_and_does_not_leak_between_numbers():
    for _ in range(5):
        login("+2347000000008", "10.0.3.1")

    assert login("+2347000000008", "10.0.3.2").status_code == 429
    assert login("+2347000000009", "10.0.3.2").status_code == 401


def test_register_gym_rate_is_configurable():
    rates = api_settings.DEFAULT_THROTTLE_RATES
    original = rates["register_gym"]
    rates["register_gym"] = "2/hour"
    try:
        first = register(40, "10.0.0.40")
        second = register(41, "10.0.0.40")
        third = register(42, "10.0.0.40")
    finally:
        rates["register_gym"] = original

    assert [first.status_code, second.status_code] == [201, 201]
    assert third.status_code == 429


def test_register_gym_is_throttled_to_five_per_hour_per_ip():
    allowed = [register(index, "10.0.0.4").status_code for index in range(5)]

    assert allowed == [201] * 5
    assert register(99, "10.0.0.4").status_code == 429
    assert Gym.objects.count() == 5


def test_register_gym_throttle_is_scoped_per_ip():
    for index in range(5):
        register(index, "10.0.0.5")

    assert register(50, "10.0.0.5").status_code == 429
    assert register(51, "10.0.0.6").status_code == 201


def test_num_proxies_defaults_to_zero():
    assert api_settings.NUM_PROXIES == 0
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == 0


def test_x_forwarded_for_is_respected_only_when_num_proxies_is_set():
    request = RequestFactory().get(
        "/api/token/", HTTP_X_FORWARDED_FOR="9.9.9.9", REMOTE_ADDR="10.0.0.5"
    )
    throttle = ScopedRateThrottle()

    with rest_framework_overrides(NUM_PROXIES=0):
        ignored = throttle.get_ident(request)
    with rest_framework_overrides(NUM_PROXIES=1):
        trusted = throttle.get_ident(request)

    assert ignored == "10.0.0.5"
    assert trusted == "9.9.9.9"


def test_x_forwarded_for_buckets_login_attempts_when_num_proxies_is_set():
    with rest_framework_overrides(NUM_PROXIES=1):
        statuses = [
            login(f"+23470030000{i:02d}", f"10.0.5.{i}", HTTP_X_FORWARDED_FOR="9.9.9.9").status_code
            for i in range(30)
        ]
        behind_same_proxy = login("+2347003000099", "10.0.5.99", HTTP_X_FORWARDED_FOR="9.9.9.9")
        different_client = login("+2347003000098", "10.0.5.98", HTTP_X_FORWARDED_FOR="8.8.8.8")

    assert set(statuses) == {401}
    assert behind_same_proxy.status_code == 429
    assert different_client.status_code == 401
