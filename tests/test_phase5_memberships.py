from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import models
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import StaffPermission, User
from gyms.models import Gym
from memberships.models import (
    MembershipPayment,
    MembershipPlan,
    MembershipSubscription,
    end_date_for,
)

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym, name="Test User"):
    return User.objects.create_user(
        phone=phone, password="password123", role=role, gym=gym, name=name
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def setup_people():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2349300000001", User.Role.OWNER, gym, "Owner")
    admin = make_user("+2349300000002", User.Role.ADMIN, gym, "Admin")
    trainer = make_user("+2349300000003", User.Role.TRAINER, gym, "Trainer")
    member = make_user("+2349300000004", User.Role.MEMBER, gym, "Member")
    other_member = make_user("+2349300000005", User.Role.MEMBER, gym, "Other Member")
    return gym, owner, admin, trainer, member, other_member


def make_plan(gym, name="Monthly", price="15000.00", duration=30, is_active=True):
    return MembershipPlan.objects.create(
        gym=gym,
        name=name,
        price=Decimal(price),
        duration_days=duration,
        is_active=is_active,
    )


def make_subscription(gym, member, plan, owner, start_date=None, status=None, price=None):
    start_date = start_date or timezone.localdate()
    return MembershipSubscription.objects.create(
        gym=gym,
        member=member,
        plan=plan,
        status=status or MembershipSubscription.Status.ACTIVE,
        start_date=start_date,
        end_date=end_date_for(start_date, plan.duration_days),
        price=price or plan.price,
        created_by=owner,
    )


def grant_payments(admin):
    StaffPermission.objects.create(user=admin, code="manage_payments")


def test_owner_and_authorized_admin_can_create_plans():
    gym, owner, admin, _, _, _ = setup_people()
    grant_payments(admin)
    payload = {
        "name": "Monthly",
        "description": "Unlimited access",
        "price": "15000.00",
        "duration_days": 30,
    }

    owner_response = client_for(owner).post("/api/membership-plans/", payload, format="json")
    admin_response = client_for(admin).post(
        "/api/membership-plans/", {**payload, "name": "Weekly"}, format="json"
    )

    assert owner_response.status_code == 201
    assert admin_response.status_code == 201
    assert set(MembershipPlan.objects.values_list("gym_id", flat=True)) == {gym.pk}
    assert client_for(owner).get("/api/membership-plans/").status_code == 200


@pytest.mark.parametrize("role", [User.Role.ADMIN, User.Role.TRAINER, User.Role.MEMBER])
def test_plan_management_requires_manage_payments(role):
    _, _, admin, trainer, member, _ = setup_people()
    actor = {User.Role.ADMIN: admin, User.Role.TRAINER: trainer, User.Role.MEMBER: member}[role]
    payload = {"name": "Monthly", "price": "15000.00", "duration_days": 30}

    response = client_for(actor).post("/api/membership-plans/", payload, format="json")

    assert response.status_code == 403
    assert not MembershipPlan.objects.exists()
    assert client_for(actor).get("/api/membership-plans/").status_code == 403


def test_plan_crud_duplicate_names_and_cross_gym_access():
    gym, owner, _, _, _, _ = setup_people()
    other_gym = Gym.objects.create(name="Other Gym")
    make_user("+2349300000010", User.Role.OWNER, other_gym, "Other Owner")
    other_plan = make_plan(other_gym, name="Other Plan")
    payload = {
        "name": "Monthly",
        "description": "Unlimited access",
        "price": "15000.00",
        "duration_days": 30,
    }

    created = client_for(owner).post("/api/membership-plans/", payload, format="json")
    duplicate = client_for(owner).post("/api/membership-plans/", payload, format="json")
    updated = client_for(owner).patch(
        f"/api/membership-plans/{created.data['id']}/",
        {"price": "18000.00"},
        format="json",
    )

    assert created.status_code == 201
    assert duplicate.status_code == 400
    assert updated.status_code == 200
    assert Decimal(updated.data["price"]) == Decimal("18000.00")
    assert client_for(owner).get(f"/api/membership-plans/{other_plan.pk}/").status_code == 404
    assert [row["id"] for row in client_for(owner).get("/api/membership-plans/").data] == [
        created.data["id"]
    ]
    deleted = client_for(owner).delete(f"/api/membership-plans/{created.data['id']}/")
    assert deleted.status_code == 204


def test_plan_in_use_cannot_be_deleted():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    make_subscription(gym, member, plan, owner)

    response = client_for(owner).delete(f"/api/membership-plans/{plan.pk}/")

    assert response.status_code == 400
    assert MembershipPlan.objects.filter(pk=plan.pk).exists()


def test_create_membership_computes_dates_and_price_snapshot():
    gym, _, admin, _, member, _ = setup_people()
    grant_payments(admin)
    plan = make_plan(gym, price="20000.00", duration=30)
    start = timezone.localdate()
    payload = {"member_id": member.pk, "plan_id": plan.pk, "start_date": start.isoformat()}

    response = client_for(admin).post("/api/memberships/", payload, format="json")
    duplicate = client_for(admin).post("/api/memberships/", payload, format="json")

    assert response.status_code == 201
    membership = MembershipSubscription.objects.get(pk=response.data["id"])
    assert membership.gym == gym
    assert membership.member == member
    assert membership.plan == plan
    assert membership.start_date == start
    assert membership.end_date == end_date_for(start, 30)
    assert membership.price == Decimal("20000.00")
    assert membership.created_by == admin
    assert response.data["plan"]["name"] == "Monthly"
    assert response.data["days_left"] == 30
    assert duplicate.status_code == 400


def test_membership_rejects_cross_gym_and_non_member_targets():
    gym, owner, _, _, _, _ = setup_people()
    other_gym = Gym.objects.create(name="Other Gym")
    other_owner = make_user("+2349300000011", User.Role.OWNER, other_gym, "Other Owner")
    other_member = make_user("+2349300000012", User.Role.MEMBER, other_gym, "Other Member")
    other_plan = make_plan(other_gym, name="Other Plan")
    plan = make_plan(gym)
    trainer = User.objects.get(phone="+2349300000003")

    cross_member = client_for(owner).post(
        "/api/memberships/", {"member_id": other_member.pk, "plan_id": plan.pk}, format="json"
    )
    non_member = client_for(owner).post(
        "/api/memberships/", {"member_id": trainer.pk, "plan_id": plan.pk}, format="json"
    )
    cross_plan = client_for(owner).post(
        "/api/memberships/", {"member_id": other_member.pk, "plan_id": other_plan.pk}, format="json"
    )
    cross_gym_owner = client_for(other_owner).post(
        "/api/memberships/", {"member_id": other_member.pk, "plan_id": plan.pk}, format="json"
    )

    assert cross_member.status_code == 404
    assert non_member.status_code == 404
    assert cross_plan.status_code == 404
    assert cross_gym_owner.status_code == 404
    assert not MembershipSubscription.objects.exists()


def test_renew_active_membership_extends_from_end_and_can_change_plan():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym, name="Monthly", price="15000.00", duration=30)
    next_plan = make_plan(gym, name="Weekly", price="5000.00", duration=7)
    start = timezone.localdate()
    membership = make_subscription(gym, member, plan, owner, start_date=start)
    old_end = membership.end_date

    response = client_for(owner).post(
        f"/api/memberships/{membership.pk}/renew/",
        {"plan_id": next_plan.pk},
        format="json",
    )

    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.status == MembershipSubscription.Status.ACTIVE
    assert membership.plan == next_plan
    assert membership.price == Decimal("5000.00")
    assert membership.start_date == old_end + timedelta(days=1)
    assert membership.end_date == end_date_for(membership.start_date, 7)


def test_renew_expired_membership_starts_today():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    yesterday = timezone.localdate() - timedelta(days=1)
    membership = make_subscription(
        gym,
        member,
        plan,
        owner,
        start_date=yesterday - timedelta(days=29),
        status=MembershipSubscription.Status.EXPIRED,
    )

    response = client_for(owner).post(f"/api/memberships/{membership.pk}/renew/", {}, format="json")

    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.start_date == timezone.localdate()
    assert membership.end_date == end_date_for(timezone.localdate(), 30)
    assert membership.status == MembershipSubscription.Status.ACTIVE


def test_cancel_membership_and_hide_it_from_staff_without_payment_permission():
    gym, owner, admin, trainer, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)

    cancelled = client_for(owner).post(f"/api/memberships/{membership.pk}/cancel/")

    assert cancelled.status_code == 200
    assert cancelled.data["status"] == "cancelled"
    assert client_for(admin).get("/api/memberships/").status_code == 403
    assert client_for(trainer).get("/api/memberships/").status_code == 403
    assert client_for(member).get("/api/memberships/").status_code == 403


def test_member_can_only_view_own_membership_and_payments():
    gym, owner, _, _, member, other_member = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    make_subscription(gym, other_member, plan, owner)
    MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        method=MembershipPayment.Method.CASH,
        recorded_by=owner,
    )
    MembershipPayment.objects.create(
        gym=gym,
        member=other_member,
        amount=Decimal("9000.00"),
        method=MembershipPayment.Method.TRANSFER,
        recorded_by=owner,
    )

    response = client_for(member).get("/api/me/membership/")

    assert response.status_code == 200
    assert response.data["subscription"]["id"] == membership.pk
    assert [row["member"]["id"] for row in response.data["payments"]] == [member.pk]
    assert client_for(owner).get("/api/me/membership/").status_code == 403


def test_payment_creation_defaults_to_membership_price():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym, price="17500.00")
    membership = make_subscription(gym, member, plan, owner)

    response = client_for(owner).post(
        "/api/membership-payments/",
        {
            "member_id": member.pk,
            "subscription_id": membership.pk,
            "method": MembershipPayment.Method.TRANSFER,
            "reference": "TRX-100",
        },
        format="json",
    )

    assert response.status_code == 201
    payment = MembershipPayment.objects.get(pk=response.data["id"])
    assert payment.gym == gym
    assert payment.member == member
    assert payment.subscription == membership
    assert payment.amount == Decimal("17500.00")
    assert payment.recorded_by == owner
    assert response.data["member"]["id"] == member.pk
    assert response.data["subscription"] == membership.pk
    assert [
        row["id"]
        for row in client_for(owner).get("/api/membership-payments/", {"member": member.pk}).data
    ] == [payment.pk]


def test_payment_requires_amount_without_membership_and_matching_member():
    gym, owner, _, _, member, other_member = setup_people()
    plan = make_plan(gym)
    other_membership = make_subscription(gym, other_member, plan, owner)

    missing_amount = client_for(owner).post(
        "/api/membership-payments/", {"member_id": member.pk}, format="json"
    )
    wrong_subscription = client_for(owner).post(
        "/api/membership-payments/",
        {"member_id": member.pk, "subscription_id": other_membership.pk, "amount": "1000.00"},
        format="json",
    )

    assert missing_amount.status_code == 400
    assert wrong_subscription.status_code == 404
    assert not MembershipPayment.objects.exists()


def test_cross_gym_memberships_and_payments_do_not_leak():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    other_gym = Gym.objects.create(name="Other Gym")
    other_owner = make_user("+2349300000013", User.Role.OWNER, other_gym, "Other Owner")
    other_member = make_user("+2349300000014", User.Role.MEMBER, other_gym, "Other Member")
    other_plan = make_plan(other_gym, name="Other Plan")
    other_membership = make_subscription(other_gym, other_member, other_plan, other_owner)
    other_payment = MembershipPayment.objects.create(
        gym=other_gym,
        member=other_member,
        subscription=other_membership,
        amount=Decimal("5000.00"),
        recorded_by=other_owner,
    )

    assert [row["id"] for row in client_for(owner).get("/api/memberships/").data] == [membership.pk]
    assert client_for(owner).get(f"/api/memberships/{other_membership.pk}/").status_code == 404
    assert [row["id"] for row in client_for(owner).get("/api/membership-payments/").data] == [
        payment.pk
    ]
    assert client_for(owner).get(f"/api/membership-payments/{other_payment.pk}/").status_code == 404
    assert client_for(owner).get(f"/api/membership-payments/?member={other_member.pk}").data == []


def test_expired_memberships_are_refreshed_on_staff_list_and_member_view():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(
        gym,
        member,
        plan,
        owner,
        start_date=timezone.localdate() - timedelta(days=31),
        status=MembershipSubscription.Status.ACTIVE,
    )

    staff_response = client_for(owner).get("/api/memberships/")
    member_response = client_for(member).get("/api/me/membership/")

    assert staff_response.status_code == 200
    assert staff_response.data[0]["status"] == MembershipSubscription.Status.EXPIRED
    assert staff_response.data[0]["days_left"] == 0
    assert member_response.data["subscription"]["status"] == MembershipSubscription.Status.EXPIRED
    membership.refresh_from_db()
    assert membership.status == MembershipSubscription.Status.EXPIRED


def test_payment_list_requires_manage_payments():
    _, _, admin, trainer, member, _ = setup_people()

    assert client_for(admin).get("/api/membership-payments/").status_code == 403
    assert client_for(trainer).get("/api/membership-payments/").status_code == 403
    assert client_for(member).get("/api/membership-payments/").status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/membership-plans/", None),
        ("post", "/api/membership-plans/", {"name": "Monthly", "price": "1", "duration_days": 1}),
        ("get", "/api/memberships/", None),
        ("post", "/api/memberships/", {"member_id": 1, "plan_id": 1}),
        ("get", "/api/memberships/1/", None),
        ("post", "/api/memberships/1/renew/", {}),
        ("post", "/api/memberships/1/cancel/", None),
        ("get", "/api/membership-payments/", None),
        ("post", "/api/membership-payments/", {"member_id": 1, "amount": "1"}),
        ("get", "/api/me/membership/", None),
    ],
)
def test_membership_endpoints_require_authentication(method, path, payload):
    response = getattr(APIClient(), method)(path, payload, format="json")

    assert response.status_code == 401


def test_money_fields_use_decimal_not_float():
    money_fields = [
        (MembershipPlan, "price"),
        (MembershipSubscription, "price"),
        (MembershipPayment, "amount"),
    ]

    for model, field_name in money_fields:
        field = model._meta.get_field(field_name)
        assert isinstance(
            field, models.DecimalField
        ), f"{model.__name__}.{field_name} is not Decimal"
        assert field.decimal_places == 2

    for model in (MembershipPlan, MembershipSubscription, MembershipPayment):
        assert not [
            field.name for field in model._meta.get_fields() if isinstance(field, models.FloatField)
        ], f"{model.__name__} must not use FloatField for money"


def test_payments_cannot_be_edited_or_deleted():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    client = client_for(owner)

    patch_response = client.patch(
        f"/api/membership-payments/{payment.pk}/", {"amount": "1.00"}, format="json"
    )
    put_response = client.put(
        f"/api/membership-payments/{payment.pk}/", {"amount": "1.00"}, format="json"
    )
    delete_response = client.delete(f"/api/membership-payments/{payment.pk}/")

    assert patch_response.status_code == 405
    assert put_response.status_code == 405
    assert delete_response.status_code == 405
    payment.refresh_from_db()
    assert payment.amount == Decimal("15000.00")
    assert not payment.is_voided


def test_void_payment_records_who_when_and_why():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    client = client_for(owner)

    response = client.post(
        f"/api/membership-payments/{payment.pk}/void/",
        {"reason": "Recorded against the wrong member"},
        format="json",
    )
    second = client.post(
        f"/api/membership-payments/{payment.pk}/void/", {"reason": "again"}, format="json"
    )

    assert response.status_code == 200
    assert second.status_code == 400
    payment.refresh_from_db()
    assert payment.is_voided
    assert payment.voided_by == owner
    assert payment.voided_at is not None
    assert payment.void_reason == "Recorded against the wrong member"
    assert payment.amount == Decimal("15000.00")
    assert MembershipPayment.objects.filter(pk=payment.pk).exists()
    assert response.data["is_voided"] is True
    assert response.data["void_reason"] == "Recorded against the wrong member"


def test_void_requires_reason_and_payment_permission():
    gym, owner, admin, trainer, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    url = f"/api/membership-payments/{payment.pk}/void/"

    blank_reason = client_for(owner).post(url, {"reason": ""}, format="json")
    missing_reason = client_for(owner).post(url, {}, format="json")
    trainer_response = client_for(trainer).post(url, {"reason": "x"}, format="json")
    member_response = client_for(member).post(url, {"reason": "x"}, format="json")
    admin_response = client_for(admin).post(url, {"reason": "x"}, format="json")

    assert blank_reason.status_code == 400
    assert missing_reason.status_code == 400
    assert trainer_response.status_code == 403
    assert member_response.status_code == 403
    assert admin_response.status_code == 403
    payment.refresh_from_db()
    assert not payment.is_voided


def test_receipt_numbers_are_unique_per_gym():
    first_gym, first_owner, _, _, first_member, _ = setup_people()
    second_gym = Gym.objects.create(name="Second Gym")
    second_owner = make_user("+2349300000020", User.Role.OWNER, second_gym, "Second Owner")
    second_member = make_user("+2349300000021", User.Role.MEMBER, second_gym, "Second Member")
    plan = make_plan(first_gym)
    second_plan = make_plan(second_gym, name="Second Plan")
    membership = make_subscription(first_gym, first_member, plan, first_owner)
    second_membership = make_subscription(second_gym, second_member, second_plan, second_owner)

    for _ in range(2):
        MembershipPayment.objects.create(
            gym=first_gym,
            member=first_member,
            subscription=membership,
            amount=Decimal("15000.00"),
            recorded_by=first_owner,
        )
    MembershipPayment.objects.create(
        gym=second_gym,
        member=second_member,
        subscription=second_membership,
        amount=Decimal("10000.00"),
        recorded_by=second_owner,
    )

    first_numbers = list(
        MembershipPayment.objects.filter(gym=first_gym)
        .order_by("id")
        .values_list("receipt_number", flat=True)
    )
    second_numbers = list(
        MembershipPayment.objects.filter(gym=second_gym).values_list("receipt_number", flat=True)
    )

    assert first_numbers == ["RC-0001-00001", "RC-0001-00002"]
    assert second_numbers == [f"RC-{second_gym.pk:04d}-00001"]
    assert len(set(first_numbers)) == 2
    constraint_names = {constraint.name for constraint in MembershipPayment._meta.constraints}
    assert "unique_receipt_number_per_gym" in constraint_names


def test_payment_receipt_number_is_exposed_and_read_only():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)

    created = client_for(owner).post(
        "/api/membership-payments/",
        {"member_id": member.pk, "subscription_id": membership.pk, "receipt_number": "FORGED"},
        format="json",
    )

    assert created.status_code == 201
    assert created.data["receipt_number"] != "FORGED"
    assert created.data["receipt_number"] == f"RC-{gym.pk:04d}-00001"
    assert created.data["is_voided"] is False


@pytest.mark.parametrize("role", [User.Role.TRAINER, User.Role.MEMBER])
def test_membership_and_payment_paths_denied_for_trainer_and_member(role):
    gym, owner, _, trainer, member, _ = setup_people()
    actor = {User.Role.TRAINER: trainer, User.Role.MEMBER: member}[role]
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    client = client_for(actor)

    assert client.get("/api/membership-plans/").status_code == 403
    assert client.get("/api/memberships/").status_code == 403
    assert client.get(f"/api/memberships/{membership.pk}/").status_code == 403
    assert (
        client.post(
            "/api/memberships/", {"member_id": member.pk, "plan_id": plan.pk}, format="json"
        ).status_code
        == 403
    )
    renew = client.post(f"/api/memberships/{membership.pk}/renew/", {}, format="json")
    cancel = client.post(f"/api/memberships/{membership.pk}/cancel/")
    assert renew.status_code == 403
    assert cancel.status_code == 403
    assert client.get("/api/membership-payments/").status_code == 403
    assert client.get(f"/api/membership-payments/{payment.pk}/").status_code == 403
    assert (
        client.post(
            "/api/membership-payments/", {"member_id": member.pk, "amount": "1.00"}, format="json"
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/membership-payments/{payment.pk}/void/", {"reason": "x"}, format="json"
        ).status_code
        == 403
    )
    assert not payment.is_voided


def test_cross_gym_payment_void_and_membership_retrieve_return_404():
    gym, owner, _, _, member, _ = setup_people()
    plan = make_plan(gym)
    membership = make_subscription(gym, member, plan, owner)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=membership,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    other_gym = Gym.objects.create(name="Other Gym")
    other_owner = make_user("+2349300000030", User.Role.OWNER, other_gym, "Other Owner")
    other_member = make_user("+2349300000031", User.Role.MEMBER, other_gym, "Other Member")
    other_plan = make_plan(other_gym, name="Other Plan")
    other_membership = make_subscription(other_gym, other_member, other_plan, other_owner)
    client = client_for(other_owner)

    assert client.get(f"/api/memberships/{membership.pk}/").status_code == 404
    assert client.get(f"/api/membership-payments/{payment.pk}/").status_code == 404
    assert (
        client.post(
            f"/api/membership-payments/{payment.pk}/void/", {"reason": "x"}, format="json"
        ).status_code
        == 404
    )
    cross_cancel = client.post(f"/api/memberships/{membership.pk}/cancel/")
    cross_renew = client.post(f"/api/memberships/{membership.pk}/renew/", {}, format="json")
    assert cross_cancel.status_code == 404
    assert cross_renew.status_code == 404
    payment.refresh_from_db()
    assert not payment.is_voided
    assert not MembershipSubscription.objects.filter(pk=membership.pk, status="cancelled").exists()
    assert other_membership.gym == other_gym


def test_deactivated_user_is_blocked_from_membership_endpoints():
    gym, owner, admin, _, _, _ = setup_people()
    grant_payments(admin)
    login = APIClient().post(
        "/api/token/", {"phone": owner.phone, "password": "password123"}, format="json"
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    assert client.get("/api/membership-plans/").status_code == 200

    owner.is_active = False
    owner.save(update_fields=["is_active"])

    assert client.get("/api/membership-plans/").status_code == 401
    assert client.get("/api/membership-payments/").status_code == 401
    plan_payload = {"name": "X", "price": "1", "duration_days": 1}
    assert client.post("/api/membership-plans/", plan_payload, format="json").status_code == 401
