import hashlib
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Invite, StaffPermission, User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


def test_register_gym_creates_owner_and_rejects_duplicate_phone():
    client = APIClient()
    payload = {
        "name": "Ada Owner",
        "phone": "+2348100000001",
        "password": "strong-password",
        "gym_name": "Ada Fitness",
    }

    response = client.post("/api/auth/register-gym/", payload, format="json")

    assert response.status_code == 201
    gym = Gym.objects.get(pk=response.data["gym_id"])
    owner = User.objects.get(pk=response.data["user_id"])
    assert gym.name == "Ada Fitness"
    assert (owner.name, owner.phone, owner.gym, owner.role) == (
        "Ada Owner",
        payload["phone"],
        gym,
        User.Role.OWNER,
    )
    assert owner.check_password(payload["password"])
    assert client.post("/api/auth/register-gym/", payload, format="json").status_code == 400
    assert Gym.objects.filter(name="Ada Fitness").count() == 1


@pytest.mark.parametrize("role", [Invite.Role.ADMIN, Invite.Role.TRAINER, Invite.Role.MEMBER])
def test_owner_can_invite_each_allowed_role(role):
    owner = make_user("+2348100000010", role=User.Role.OWNER)
    response = invite_client(owner).post(
        "/api/invites/",
        {"role": role, "phone": phone_for(role), "name": "New staff"},
        format="json",
    )

    assert response.status_code == 201
    invite = Invite.objects.get(pk=response.data["id"])
    assert invite.role == role
    assert invite.token_hash == hashlib.sha256(response.data["code"].encode()).hexdigest()
    assert response.data["code"] not in invite.token_hash


@pytest.mark.parametrize(
    ("role", "permission"),
    [(Invite.Role.TRAINER, "manage_trainers"), (Invite.Role.MEMBER, "manage_members")],
)
def test_admin_with_matching_permission_can_invite_allowed_role(role, permission):
    admin = make_user("+2348100000020", role=User.Role.ADMIN)
    StaffPermission.objects.create(user=admin, code=permission)

    response = invite_client(admin).post(
        "/api/invites/", {"role": role, "phone": phone_for(role), "name": "Invitee"}, format="json"
    )

    assert response.status_code == 201
    assert Invite.objects.get(pk=response.data["id"]).gym == admin.gym


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (Invite.Role.ADMIN, "manage_trainers"),
        (Invite.Role.TRAINER, "manage_members"),
        (Invite.Role.MEMBER, "manage_trainers"),
    ],
)
def test_admin_cannot_invite_above_level_or_without_matching_permission(role, permission):
    admin = make_user("+2348100000030", role=User.Role.ADMIN)
    StaffPermission.objects.create(user=admin, code=permission)

    response = invite_client(admin).post(
        "/api/invites/", {"role": role, "phone": phone_for(role), "name": "Invitee"}, format="json"
    )

    assert response.status_code == 403
    assert not Invite.objects.exists()


@pytest.mark.parametrize("role", [Invite.Role.ADMIN, Invite.Role.TRAINER, Invite.Role.MEMBER])
def test_trainer_and_member_cannot_invite_any_role(role):
    for inviter_role, suffix in [(User.Role.TRAINER, "4"), (User.Role.MEMBER, "5")]:
        inviter = make_user(f"+23481000000{suffix}0", role=inviter_role)
        response = invite_client(inviter).post(
            "/api/invites/",
            {"role": role, "phone": f"+23482000000{suffix}{role[-1]}", "name": "Invitee"},
            format="json",
        )
        assert response.status_code == 403
    assert not Invite.objects.exists()


def test_invite_code_is_single_use_and_invalid_reasons_share_generic_error():
    invite, code = make_invite("+2348100000060")
    client = APIClient()
    redeemed = client.post(
        "/api/auth/redeem-invite/", {"code": code, "password": "strong-password"}, format="json"
    )
    assert redeemed.status_code == 200
    user = User.objects.get(phone=invite.phone)
    assert user.gym == invite.gym
    assert user.role == invite.role
    invite.refresh_from_db()
    assert invite.used_at is not None
    assert client.post(
        "/api/auth/redeem-invite/", {"code": code, "password": "another-password"}, format="json"
    ).data == {"detail": "Invite code is invalid or unavailable."}
    assert client.post(
        "/api/auth/redeem-invite/",
        {"code": "wrong-code", "password": "strong-password"},
        format="json",
    ).data == {"detail": "Invite code is invalid or unavailable."}


def test_expired_invite_uses_generic_error():
    invite, code = make_invite("+2348100000061")
    Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    response = APIClient().post(
        "/api/auth/redeem-invite/", {"code": code, "password": "strong-password"}, format="json"
    )

    assert response.status_code == 400
    assert response.data == {"detail": "Invite code is invalid or unavailable."}
    assert not User.objects.filter(phone=invite.phone).exists()


def test_invite_redemption_always_uses_invites_gym():
    gym_a = Gym.objects.create(name="Gym A")
    gym_b = Gym.objects.create(name="Gym B")
    invite, code = make_invite("+2348100000062", gym=gym_b)

    response = APIClient().post(
        "/api/auth/redeem-invite/", {"code": code, "password": "strong-password"}, format="json"
    )

    user = User.objects.get(phone=invite.phone)
    assert response.status_code == 200
    assert user.gym_id == gym_b.pk
    assert user.gym_id != gym_a.pk


def test_invite_expires_seven_days_after_creation_by_default():
    invite, _ = make_invite("+2348100000064")

    assert timedelta(days=7) - timedelta(seconds=2) <= invite.expires_at - invite.created_at
    assert invite.expires_at - invite.created_at <= timedelta(days=7) + timedelta(seconds=2)


def test_admin_cannot_redeem_invite_for_phone_already_registered_in_another_gym():
    gym_a = Gym.objects.create(name="Gym A")
    gym_b = Gym.objects.create(name="Gym B")
    User.objects.create_user(phone="+2348100000063", password="old-password", gym=gym_a)
    invite, code = make_invite("+2348100000063", gym=gym_b)

    response = APIClient().post(
        "/api/auth/redeem-invite/", {"code": code, "password": "strong-password"}, format="json"
    )

    assert response.status_code == 400
    assert User.objects.get(phone=invite.phone).gym_id == gym_a.pk
    invite.refresh_from_db()
    assert invite.used_at is None


def make_user(phone, role, gym=None):
    return User.objects.create_user(
        phone=phone,
        password="strong-password",
        name="Test User",
        gym=gym or Gym.objects.create(name=f"Gym {phone}"),
        role=role,
    )


def invite_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_invite(phone, gym=None):
    gym = gym or Gym.objects.create(name=f"Invite Gym {phone}")
    creator = make_user(f"+234830000{phone[-4:]}", User.Role.OWNER, gym=gym)
    code = "test-code-" + phone[-4:]
    invite = Invite.objects.create(
        gym=gym,
        role=Invite.Role.TRAINER,
        phone=phone,
        name="Invited User",
        created_by=creator,
        token_hash=hashlib.sha256(code.encode()).hexdigest(),
    )
    cache.clear()
    return invite, code


def phone_for(role):
    return f"+234840000000{list(Invite.Role.values).index(role)}"
