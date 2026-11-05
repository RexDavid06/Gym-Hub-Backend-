import pytest
from rest_framework.test import APIClient

from accounts.models import StaffPermission, User
from gyms.models import Branch, Gym

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym, name="Test User"):
    return User.objects.create_user(
        phone=phone, password="password123", role=role, gym=gym, name=name
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def setup_gym():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2349200000001", User.Role.OWNER, gym, "Owner")
    admin = make_user("+2349200000002", User.Role.ADMIN, gym, "Admin")
    trainer = make_user("+2349200000003", User.Role.TRAINER, gym, "Trainer")
    member = make_user("+2349200000004", User.Role.MEMBER, gym, "Member")
    return gym, owner, admin, trainer, member


def branch_url(branch):
    return f"/api/branches/{branch.pk}/"


def user_branch_url(user):
    return f"/api/users/{user.pk}/branch/"


def test_owner_can_create_list_update_and_delete_branches():
    gym, owner, _, _, _ = setup_gym()
    client = client_for(owner)

    created = client.post(
        "/api/branches/",
        {"name": "Main Branch", "address": "12 Adeola Odeku Street"},
        format="json",
    )

    assert created.status_code == 201
    branch_id = created.data["id"]
    assert created.data["name"] == "Main Branch"
    assert Branch.objects.get(pk=branch_id).gym == gym

    listed = client.get("/api/branches/")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.data] == [branch_id]

    updated = client.patch(
        branch_url(Branch.objects.get(pk=branch_id)),
        {"address": "New address"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["address"] == "New address"

    deleted = client.delete(branch_url(Branch.objects.get(pk=branch_id)))
    assert deleted.status_code == 204
    assert not Branch.objects.filter(pk=branch_id).exists()


def test_branch_names_are_unique_per_gym_only():
    gym, owner, _, _, _ = setup_gym()
    other_gym = Gym.objects.create(name="Other Gym")
    other_owner = make_user("+2349200000101", User.Role.OWNER, other_gym, "Other Owner")
    client = client_for(owner)
    other_client = client_for(other_owner)

    assert client.post("/api/branches/", {"name": "Central"}, format="json").status_code == 201
    duplicate = client.post("/api/branches/", {"name": "Central"}, format="json")
    assert duplicate.status_code == 400
    assert "name" in duplicate.data

    assert (
        other_client.post("/api/branches/", {"name": "Central"}, format="json").status_code == 201
    )


def test_admins_can_read_branches_but_only_owners_can_change_them():
    gym, owner, admin, _, _ = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    StaffPermission.objects.create(user=admin, code="manage_members")
    client = client_for(admin)

    assert client.get("/api/branches/").status_code == 200
    assert client.post("/api/branches/", {"name": "Annex"}, format="json").status_code == 403
    assert client.patch(branch_url(branch), {"name": "Renamed"}, format="json").status_code == 403
    assert client.delete(branch_url(branch)).status_code == 403


@pytest.mark.parametrize("role", [User.Role.TRAINER, User.Role.MEMBER])
def test_trainers_and_members_cannot_list_or_change_branches(role):
    gym, owner, _, trainer, member = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    actor = trainer if role == User.Role.TRAINER else member
    client = client_for(actor)

    assert client.get("/api/branches/").status_code == 403
    assert client.post("/api/branches/", {"name": "Annex"}, format="json").status_code == 403
    assert client.patch(branch_url(branch), {"name": "Renamed"}, format="json").status_code == 403
    assert client.delete(branch_url(branch)).status_code == 403


def test_owner_can_assign_and_clear_user_branches():
    gym, owner, admin, trainer, member = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    other_branch = Branch.objects.create(gym=gym, name="Annex")
    client = client_for(owner)

    for user in (admin, trainer, member):
        response = client.put(user_branch_url(user), {"branch_id": branch.pk}, format="json")
        assert response.status_code == 200
        assert response.data["branch"]["id"] == branch.pk
        user.refresh_from_db()
        assert user.branch == branch

    reassigned = client.put(user_branch_url(member), {"branch_id": other_branch.pk}, format="json")
    assert reassigned.status_code == 200
    member.refresh_from_db()
    assert member.branch == other_branch

    cleared = client.put(user_branch_url(member), {"branch_id": None}, format="json")
    assert cleared.status_code == 200
    assert cleared.data["branch"] is None
    member.refresh_from_db()
    assert member.branch is None


def test_user_list_and_me_include_branch():
    gym, owner, _, _, member = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    member.branch = branch
    member.save(update_fields=["branch"])

    listed = client_for(owner).get("/api/users/?role=member")
    assert listed.status_code == 200
    assert listed.data[0]["branch"] == {"id": branch.pk, "name": "Main"}

    me = client_for(member).get("/api/me/")
    assert me.status_code == 200
    assert me.data["branch"] == {"id": branch.pk, "name": "Main"}


def test_admin_branch_assignment_follows_staff_permissions():
    gym, owner, admin, trainer, member = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    client = client_for(admin)

    assert (
        client.put(user_branch_url(member), {"branch_id": branch.pk}, format="json").status_code
        == 403
    )

    StaffPermission.objects.create(user=admin, code="manage_members")
    allowed = client.put(user_branch_url(member), {"branch_id": branch.pk}, format="json")
    assert allowed.status_code == 200
    assert (
        client.put(user_branch_url(trainer), {"branch_id": branch.pk}, format="json").status_code
        == 403
    )
    assert (
        client.put(user_branch_url(owner), {"branch_id": branch.pk}, format="json").status_code
        == 403
    )

    StaffPermission.objects.create(user=admin, code="manage_trainers")
    assert (
        client.put(user_branch_url(trainer), {"branch_id": branch.pk}, format="json").status_code
        == 200
    )


@pytest.mark.parametrize("role", [User.Role.TRAINER, User.Role.MEMBER])
def test_trainers_and_members_cannot_assign_branches(role):
    gym, owner, _, trainer, member = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    actor = trainer if role == User.Role.TRAINER else member
    target = member if role == User.Role.TRAINER else trainer

    response = client_for(actor).put(
        user_branch_url(target), {"branch_id": branch.pk}, format="json"
    )

    assert response.status_code == 403
    target.refresh_from_db()
    assert target.branch is None


def test_branch_assignment_is_gym_scoped():
    gym, owner, _, _, member = setup_gym()
    other_gym = Gym.objects.create(name="Other Gym")
    make_user("+2349200000201", User.Role.OWNER, other_gym, "Other Owner")
    other_member = make_user("+2349200000202", User.Role.MEMBER, other_gym, "Other Member")
    own_branch = Branch.objects.create(gym=gym, name="Main")
    other_branch = Branch.objects.create(gym=other_gym, name="Other Main")
    client = client_for(owner)

    assert (
        client.put(
            user_branch_url(member), {"branch_id": other_branch.pk}, format="json"
        ).status_code
        == 404
    )
    assert (
        client.put(
            user_branch_url(other_member), {"branch_id": own_branch.pk}, format="json"
        ).status_code
        == 404
    )
    assert client.get(branch_url(other_branch)).status_code == 404
    assert client.delete(branch_url(other_branch)).status_code == 404
    assert Branch.objects.filter(pk=other_branch.pk).exists()


def test_branch_list_is_scoped_to_the_request_gym():
    gym, owner, _, _, _ = setup_gym()
    other_gym = Gym.objects.create(name="Other Gym")
    own = Branch.objects.create(gym=gym, name="Main")
    Branch.objects.create(gym=other_gym, name="Other Main")

    listed = client_for(owner).get("/api/branches/")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.data] == [own.pk]


def test_deleting_a_branch_clears_user_assignments():
    gym, owner, _, _, member = setup_gym()
    branch = Branch.objects.create(gym=gym, name="Main")
    member.branch = branch
    member.save(update_fields=["branch"])

    assert client_for(owner).delete(branch_url(branch)).status_code == 204

    member.refresh_from_db()
    assert member.branch is None


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/branches/", None),
        ("post", "/api/branches/", {"name": "Main"}),
        ("get", "/api/branches/1/", None),
        ("put", "/api/users/1/branch/", {"branch_id": None}),
    ],
)
def test_branch_endpoints_require_authentication(method, path, payload):
    response = getattr(APIClient(), method)(path, payload, format="json")

    assert response.status_code == 401
