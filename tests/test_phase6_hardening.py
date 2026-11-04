import hashlib
import secrets

import pytest
from django.db import connection
from rest_framework.test import APIClient

from accounts.models import Invite, StaffPermission, User
from gyms.models import Gym
from memberships.models import MembershipPlan, MembershipSubscription

STRONG_PASSWORD = "Str0ng-Passw0rd!42"

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym, name="Test User", is_active=True, password=STRONG_PASSWORD):
    return User.objects.create_user(
        phone=phone,
        password=password,
        role=role,
        gym=gym,
        name=name,
        is_active=is_active,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def register_gym(password, phone="+2347600000001"):
    return APIClient().post(
        "/api/auth/register-gym/",
        {
            "name": "Ada",
            "phone": phone,
            "password": password,
            "gym_name": "Ada's Gym",
        },
        format="json",
    )


def make_invite(gym, created_by, phone="+2347600000101"):
    code = secrets.token_urlsafe(16)
    invite = Invite.objects.create(
        gym=gym,
        role=Invite.Role.MEMBER,
        phone=phone,
        name="Invited Member",
        created_by=created_by,
        token_hash=hashlib.sha256(code.encode()).hexdigest(),
    )
    return invite, code


def payment_only_admin(gym):
    admin = make_user("+2347600000401", User.Role.ADMIN, gym, "Payments Admin")
    StaffPermission.objects.create(user=admin, code="manage_payments")
    return admin


@pytest.mark.parametrize(
    "password",
    ["short", "12345678901", "password1"],
    ids=["too-short", "numeric-only", "common"],
)
def test_gym_registration_rejects_weak_passwords(password):
    response = register_gym(password)
    assert response.status_code == 400
    assert "password" in response.data
    assert Gym.objects.count() == 0


def test_gym_registration_accepts_a_strong_password():
    response = register_gym(STRONG_PASSWORD)
    assert response.status_code == 201
    user = User.objects.get(phone="+2347600000001")
    assert user.check_password(STRONG_PASSWORD)


@pytest.mark.parametrize(
    "password",
    ["short", "12345678901", "password1"],
    ids=["too-short", "numeric-only", "common"],
)
def test_invite_redemption_rejects_weak_passwords(password):
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2347600000201", User.Role.OWNER, gym, "Owner")
    invite, code = make_invite(gym, owner)
    response = APIClient().post(
        "/api/auth/redeem-invite/", {"code": code, "password": password}, format="json"
    )
    assert response.status_code == 400
    assert "password" in response.data
    invite.refresh_from_db()
    assert invite.used_at is None
    assert not User.objects.filter(phone=invite.phone).exists()


def test_invite_redemption_accepts_a_strong_password_and_logs_in():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2347600000301", User.Role.OWNER, gym, "Owner")
    invite, code = make_invite(gym, owner)
    response = APIClient().post(
        "/api/auth/redeem-invite/", {"code": code, "password": STRONG_PASSWORD}, format="json"
    )
    assert response.status_code == 200
    assert response.data["access"]
    assert response.data["refresh"]
    assert User.objects.filter(phone=invite.phone, gym=gym, role=User.Role.MEMBER).exists()


def test_payment_only_admin_can_list_members_but_not_other_roles():
    gym = Gym.objects.create(name="Gym")
    make_user("+2347600000501", User.Role.OWNER, gym, "Owner")
    admin = payment_only_admin(gym)
    member = make_user("+2347600000502", User.Role.MEMBER, gym, "Member")
    make_user("+2347600000503", User.Role.TRAINER, gym, "Trainer")
    client = client_for(admin)
    listed = client.get("/api/users/", {"role": "member"})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.data] == [member.pk]
    assert client.get("/api/users/", {"role": "trainer"}).status_code == 403
    assert client.get("/api/users/", {"role": "admin"}).status_code == 403
    assert client.get("/api/users/", {"role": "owner"}).status_code == 403
    assert client.get("/api/users/").status_code == 403


def test_payment_only_admin_can_read_and_record_payments_but_not_assign_trainers():
    gym = Gym.objects.create(name="Gym")
    make_user("+2347600000601", User.Role.OWNER, gym, "Owner")
    admin = payment_only_admin(gym)
    member = make_user("+2347600000602", User.Role.MEMBER, gym, "Member")
    trainer = make_user("+2347600000603", User.Role.TRAINER, gym, "Trainer")
    plan = MembershipPlan.objects.create(
        gym=gym, name="Monthly", price="15000.00", duration_days=30
    )
    client = client_for(admin)
    created = client.post(
        "/api/memberships/", {"member_id": member.pk, "plan_id": plan.pk}, format="json"
    )
    assert created.status_code == 201
    subscription = MembershipSubscription.objects.get(member=member)
    assert client.get("/api/memberships/", {"member": member.pk}).status_code == 200
    payment = client.post(
        "/api/membership-payments/",
        {
            "member_id": member.pk,
            "subscription_id": subscription.pk,
            "amount": "15000.00",
        },
        format="json",
    )
    assert payment.status_code == 201
    assert client.get("/api/membership-payments/", {"member": member.pk}).status_code == 200
    assignment = client.put(
        f"/api/members/{member.pk}/trainer/", {"trainer_id": trainer.pk}, format="json"
    )
    assert assignment.status_code == 403
    assert client.get("/api/trainers/").status_code == 403


def test_payment_only_admin_unknown_member_returns_404():
    gym = Gym.objects.create(name="Gym")
    admin = payment_only_admin(gym)
    client = client_for(admin)
    assert client.get("/api/users/999999/").status_code == 404
    other_gym = Gym.objects.create(name="Other Gym")
    other_member = make_user("+2347600000611", User.Role.MEMBER, other_gym, "Outsider")
    assert client.get(f"/api/users/{other_member.pk}/").status_code == 404
    assert client.get(f"/api/memberships/{other_member.pk}/").status_code == 404


def test_payment_only_admin_cross_gym_isolation():
    gym = Gym.objects.create(name="Gym")
    other_gym = Gym.objects.create(name="Other Gym")
    make_user("+2347600000701", User.Role.OWNER, other_gym, "Other Owner")
    admin = payment_only_admin(gym)
    outsider = make_user("+2347600000702", User.Role.MEMBER, other_gym, "Outsider")
    other_plan = MembershipPlan.objects.create(
        gym=other_gym, name="Other Plan", price="9000.00", duration_days=30
    )
    client = client_for(admin)
    assert client.get("/api/users/", {"role": "member"}).data == []
    assert client.get(f"/api/memberships/?member={outsider.pk}").data == []
    assert client.get(f"/api/memberships/{outsider.pk}/").status_code == 404
    assert (
        client.post(
            "/api/memberships/",
            {"member_id": outsider.pk, "plan_id": other_plan.pk},
            format="json",
        ).status_code
        == 404
    )


def test_payment_only_admin_member_detail_flow():
    gym = Gym.objects.create(name="Gym")
    make_user("+2347600000801", User.Role.OWNER, gym, "Owner")
    admin = payment_only_admin(gym)
    member = make_user("+2347600000802", User.Role.MEMBER, gym, "Member")
    trainer = make_user("+2347600000803", User.Role.TRAINER, gym, "Trainer")
    plan = MembershipPlan.objects.create(
        gym=gym, name="Monthly", price="15000.00", duration_days=30
    )
    client = client_for(admin)
    created = client.post(
        "/api/memberships/", {"member_id": member.pk, "plan_id": plan.pk}, format="json"
    )
    assert created.status_code == 201
    subscription = MembershipSubscription.objects.get(member=member)
    payment = client.post(
        "/api/membership-payments/",
        {
            "member_id": member.pk,
            "subscription_id": subscription.pk,
            "amount": "15000.00",
        },
        format="json",
    )
    assert payment.status_code == 201
    payment_id = payment.data["id"]
    detail = client.get(f"/api/users/{member.pk}/").data
    assert detail["role"] == "member"
    assert detail["gym"] == gym.pk
    assert detail["assigned_trainer"] is None
    assert "password" not in detail
    assert client.get("/api/users/999999/").status_code == 404
    assert client.get("/api/users/", {"role": "admin"}).status_code == 403
    assert client.get("/api/users/", {"role": "owner"}).status_code == 403
    assert (
        client.put(
            f"/api/members/{member.pk}/trainer/", {"trainer_id": trainer.pk}, format="json"
        ).status_code
        == 403
    )
    assert client.get("/api/trainers/").status_code == 403
    assert client.get(f"/api/membership-payments/{payment_id}/").status_code == 200


def test_payment_only_admin_cannot_reach_other_gyms():
    gym = Gym.objects.create(name="Gym")
    other_gym = Gym.objects.create(name="Other Gym")
    make_user("+2347600000701", User.Role.OWNER, other_gym, "Other Owner")
    admin = payment_only_admin(gym)
    outsider = make_user("+2347600000702", User.Role.MEMBER, other_gym, "Outsider")
    other_plan = MembershipPlan.objects.create(
        gym=other_gym, name="Other Plan", price="9000.00", duration_days=30
    )
    client = client_for(admin)
    assert client.get("/api/users/", {"role": "member"}).data == []
    assert client.get(f"/api/memberships/?member={outsider.pk}").data == []
    assert client.get(f"/api/memberships/{outsider.pk}/").status_code == 404
    assert (
        client.post(
            "/api/memberships/",
            {"member_id": outsider.pk, "plan_id": other_plan.pk},
            format="json",
        ).status_code
        == 404
    )


def test_trainer_and_member_cannot_list_users():
    gym = Gym.objects.create(name="Gym")
    trainer = make_user("+2347600000801", User.Role.TRAINER, gym, "Trainer")
    member = make_user("+2347600000802", User.Role.MEMBER, gym, "Member")
    for user in (trainer, member):
        for query in ({}, {"role": "member"}):
            assert client_for(user).get("/api/users/", query).status_code == 403


def test_health_endpoint_reports_ok():
    response = APIClient().get("/api/health/")
    assert response.status_code == 200
    assert response.data == {"status": "ok"}


def test_health_endpoint_reports_unavailable_without_details(monkeypatch):
    def boom():
        raise RuntimeError("connection refused to secret-host")

    monkeypatch.setattr(connection, "cursor", boom)

    response = APIClient().get("/api/health/")
    assert response.status_code == 503
    assert response.data == {"status": "unavailable"}
    assert b"secret-host" not in response.content
