from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from gyms.models import Gym
from memberships.models import MembershipPlan, MembershipSubscription

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym=None, name="Test User", is_active=True):
    return User.objects.create_user(
        phone=phone,
        password="Str0ng-Passw0rd!42",
        role=role,
        gym=gym,
        name=name,
        is_active=is_active,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_subscription(gym, member, owner, end_date, price="15000.00"):
    plan = MembershipPlan.objects.create(
        gym=gym, name=f"Plan {member.pk}", price=price, duration_days=30
    )
    return MembershipSubscription.objects.create(
        gym=gym,
        member=member,
        plan=plan,
        start_date=end_date - timedelta(days=29),
        end_date=end_date,
        price=price,
        created_by=owner,
    )


@pytest.fixture
def super_admin():
    return make_user("+2347000000001", User.Role.SUPER_ADMIN, name="Platform")


@pytest.fixture
def gym_people():
    gym = Gym.objects.create(name="Alpha Fitness", plan_tier="small")
    owner = make_user("+2347000000101", User.Role.OWNER, gym, "Alpha Owner")
    admin = make_user("+2347000000102", User.Role.ADMIN, gym, "Alpha Admin")
    trainer = make_user("+2347000000103", User.Role.TRAINER, gym, "Alpha Trainer")
    member = make_user("+2347000000104", User.Role.MEMBER, gym, "Alpha Member")
    return gym, owner, admin, trainer, member


def test_platform_endpoints_reject_unauthenticated_and_non_super_admin(gym_people):
    gym, owner, admin, trainer, member = gym_people
    paths = [
        ("get", "/api/platform/summary/"),
        ("get", "/api/platform/gyms/"),
        ("get", f"/api/platform/gyms/{gym.pk}/"),
        ("patch", f"/api/platform/gyms/{gym.pk}/"),
    ]

    for method, path in paths:
        assert getattr(APIClient(), method)(path).status_code == 401
        for user in (owner, admin, trainer, member):
            response = getattr(client_for(user), method)(path, {"is_active": True}, format="json")
            assert response.status_code == 403, (method, path, user.role)


def test_summary_counts_across_three_gyms(super_admin):
    today = timezone.localdate()

    first = Gym.objects.create(name="One", plan_tier="small")
    first_owner = make_user("+2347100000011", User.Role.OWNER, first, "Owner One")
    first_member = make_user("+2347100000012", User.Role.MEMBER, first, "Member One")
    make_user("+2347100000013", User.Role.TRAINER, first, "Trainer One")
    make_subscription(first, first_member, first_owner, today + timedelta(days=1))
    # A deactivated member is not counted in members_total.
    make_user("+2347100000014", User.Role.MEMBER, first, "Gone", is_active=False)

    second = Gym.objects.create(name="Two", plan_tier="medium")
    second_owner = make_user("+2347100000021", User.Role.OWNER, second, "Owner Two")
    for index, days in enumerate((10, 30)):
        member = make_user(f"+234710000002{index + 2}", User.Role.MEMBER, second)
        make_user(f"+234710000002{index + 4}", User.Role.TRAINER, second)
        make_subscription(second, member, second_owner, today + timedelta(days=days))

    third = Gym.objects.create(name="Three", plan_tier="big", is_active=False)
    third_owner = make_user("+2347100000031", User.Role.OWNER, third, "Owner Three")
    for index, days in enumerate((-1, 2, 20)):
        member = make_user(f"+234710000003{index + 2}", User.Role.MEMBER, third)
        make_user(f"+234710000003{index + 5}", User.Role.TRAINER, third)
        make_subscription(third, member, third_owner, today + timedelta(days=days))

    response = client_for(super_admin).get("/api/platform/summary/")

    assert response.status_code == 200
    assert response.data == {
        "gyms_total": 3,
        "gyms_active": 2,
        "gyms_suspended": 1,
        "members_total": 6,  # 1 + 2 + 3 active members; the deactivated one is excluded
        "trainers_total": 6,  # 1 + 2 + 3
        "active_memberships": 5,  # the subscription that ended yesterday is excluded
        "memberships_expiring_7_days": 2,  # today+1 and today+2
    }


def test_gym_list_search_filter_and_pagination(super_admin):
    offices = Gym.objects.create(name="Lagos Office Gym", plan_tier="medium")
    Gym.objects.create(name="Abuja Strength", plan_tier="big")
    Gym.objects.create(name="Kano Fitness", plan_tier="small", is_active=False)
    owner = make_user("+2347200000001", User.Role.OWNER, offices, "Owner One")
    make_user("+2347200000002", User.Role.MEMBER, offices, "Member One")
    make_user("+2347200000003", User.Role.TRAINER, offices, "Trainer One")

    client = client_for(super_admin)
    everything = client.get("/api/platform/gyms/")

    assert everything.status_code == 200
    assert everything.data["count"] == 3
    assert [row["name"] for row in everything.data["results"]] == [
        "Kano Fitness",
        "Abuja Strength",
        "Lagos Office Gym",
    ]
    lagos = next(row for row in everything.data["results"] if row["id"] == offices.pk)
    assert lagos["owner"] == {"id": owner.pk, "name": "Owner One", "phone": owner.phone}
    assert lagos["member_count"] == 1
    assert lagos["trainer_count"] == 1

    search = client.get("/api/platform/gyms/", {"search": "lagos"})
    assert [row["id"] for row in search.data["results"]] == [offices.pk]

    suspended = client.get("/api/platform/gyms/", {"is_active": "false"})
    assert [row["name"] for row in suspended.data["results"]] == ["Kano Fitness"]

    tier = client.get("/api/platform/gyms/", {"plan_tier": "big"})
    assert [row["name"] for row in tier.data["results"]] == ["Abuja Strength"]

    assert client.get("/api/platform/gyms/", {"is_active": "maybe"}).status_code == 400
    assert client.get("/api/platform/gyms/", {"plan_tier": "huge"}).status_code == 400

    page = client.get("/api/platform/gyms/", {"page_size": 2})
    assert page.data["count"] == 3
    assert len(page.data["results"]) == 2
    assert page.data["next"] is not None
    second = client.get("/api/platform/gyms/", {"page_size": 2, "page": 2})
    assert len(second.data["results"]) == 1
    assert client.get("/api/platform/gyms/", {"page": 99}).status_code == 404


def test_gym_detail_reports_counts_and_404(super_admin, gym_people):
    gym, owner, admin, trainer, member = gym_people
    today = timezone.localdate()
    make_subscription(gym, member, owner, today + timedelta(days=10))
    other_member = make_user("+2347300000001", User.Role.MEMBER, gym, "Other")
    make_subscription(gym, other_member, owner, today - timedelta(days=2))

    response = client_for(super_admin).get(f"/api/platform/gyms/{gym.pk}/")

    assert response.status_code == 200
    assert response.data["name"] == "Alpha Fitness"
    assert response.data["owner"]["id"] == owner.pk
    assert response.data["admin_count"] == 1
    assert response.data["member_count"] == 2
    assert response.data["active_membership_count"] == 1
    assert response.data["expired_membership_count"] == 1
    assert response.data["suspended_at"] is None
    assert response.data["suspended_by"] is None
    assert response.data["suspension_reason"] == ""

    assert client_for(super_admin).get("/api/platform/gyms/999999/").status_code == 404


def test_owner_is_the_earliest_active_owner(super_admin):
    gym = Gym.objects.create(name="Ownership Gym")
    first = make_user("+2347400000001", User.Role.OWNER, gym, "First")
    User.objects.filter(pk=first.pk).update(date_joined=timezone.now() - timedelta(days=30))
    make_user("+2347400000002", User.Role.OWNER, gym, "Second")
    gone = make_user("+2347400000003", User.Role.OWNER, gym, "Former", is_active=False)
    User.objects.filter(pk=gone.pk).update(date_joined=timezone.now() - timedelta(days=90))

    response = client_for(super_admin).get(f"/api/platform/gyms/{gym.pk}/")

    assert response.data["owner"]["name"] == "First"


def test_suspend_and_reactivate_gym(super_admin, gym_people):
    gym, owner, *_ = gym_people
    client = client_for(super_admin)

    suspended = client.patch(
        f"/api/platform/gyms/{gym.pk}/",
        {"is_active": False, "suspension_reason": "Unpaid invoice"},
        format="json",
    )

    assert suspended.status_code == 200
    gym.refresh_from_db()
    assert gym.is_active is False
    assert gym.suspension_reason == "Unpaid invoice"
    assert gym.suspended_by == super_admin
    assert gym.suspended_at is not None
    assert suspended.data["suspended_by"]["id"] == super_admin.pk
    assert suspended.data["suspension_reason"] == "Unpaid invoice"

    reactivated = client.patch(f"/api/platform/gyms/{gym.pk}/", {"is_active": True}, format="json")

    assert reactivated.status_code == 200
    gym.refresh_from_db()
    assert gym.is_active is True
    assert gym.suspension_reason == ""
    assert gym.suspended_by is None
    assert gym.suspended_at is None


def test_suspend_requires_a_reason(super_admin, gym_people):
    gym, *_ = gym_people
    client = client_for(super_admin)

    blank = client.patch(
        f"/api/platform/gyms/{gym.pk}/",
        {"is_active": False, "suspension_reason": "   "},
        format="json",
    )
    missing = client.patch(f"/api/platform/gyms/{gym.pk}/", {"is_active": False}, format="json")

    assert blank.status_code == 400
    assert "suspension_reason" in blank.data
    assert missing.status_code == 400
    gym.refresh_from_db()
    assert gym.is_active is True


def test_plan_tier_update_and_immutable_fields(super_admin, gym_people):
    gym, *_ = gym_people
    client = client_for(super_admin)

    updated = client.patch(
        f"/api/platform/gyms/{gym.pk}/",
        {"plan_tier": "big", "name": "Renamed Gym"},
        format="json",
    )

    assert updated.status_code == 200
    gym.refresh_from_db()
    assert gym.plan_tier == "big"
    assert gym.name == "Alpha Fitness"
    assert (
        client.patch(
            f"/api/platform/gyms/{gym.pk}/", {"plan_tier": "huge"}, format="json"
        ).status_code
        == 400
    )


def test_super_admin_gets_no_tenant_data_from_tenant_endpoints(super_admin, gym_people):
    client = client_for(super_admin)

    # Tenant endpoints never widen for platform staff: they either deny the
    # role or scope to `request.user.gym_id`, which is None for a super-admin.
    for path in ("/api/users/", "/api/membership-plans/", "/api/memberships/"):
        response = client.get(path)
        assert response.status_code == 403, path
        assert "results" not in response.data, path

    assert client.get("/api/memberships/1/").status_code == 403
    assert client.get("/api/users/?role=owner").status_code == 403
    assert (
        client.post(
            "/api/membership-plans/",
            {"name": "Sneaky", "price": "1.00", "duration_days": 30},
            format="json",
        ).status_code
        == 403
    )
    assert client.get("/api/invites/").status_code == 403
