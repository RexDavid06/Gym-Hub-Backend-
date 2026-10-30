import pytest
from rest_framework.test import APIClient

from accounts.models import StaffPermission, TrainerAssignment, User
from gyms.models import Gym

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym, name="Test User"):
    return User.objects.create_user(
        phone=phone, password="password123", role=role, gym=gym, name=name
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_assignment(owner, member, trainer):
    return TrainerAssignment.objects.create(
        gym=owner.gym, member=member, trainer=trainer, assigned_by=owner
    )


def setup_team():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2349100000001", User.Role.OWNER, gym, "Owner")
    admin = make_user("+2349100000002", User.Role.ADMIN, gym, "Admin")
    trainer = make_user("+2349100000003", User.Role.TRAINER, gym, "Trainer")
    member = make_user("+2349100000004", User.Role.MEMBER, gym, "Member")
    return gym, owner, admin, trainer, member


def assignment_url(member):
    return f"/api/members/{member.pk}/trainer/"


def test_owner_can_assign_reassign_and_unassign_trainer():
    gym, owner, _, trainer, member = setup_team()
    second_trainer = make_user("+2349100000010", User.Role.TRAINER, gym, "Second Trainer")
    client = client_for(owner)

    assigned = client.put(assignment_url(member), {"trainer_id": trainer.pk}, format="json")

    assert assigned.status_code == 200
    assert assigned.data["member"]["id"] == member.pk
    assert assigned.data["trainer"]["id"] == trainer.pk
    assignment = TrainerAssignment.objects.get(member=member)
    assert (assignment.gym, assignment.trainer, assignment.assigned_by) == (gym, trainer, owner)

    listed = client.get("/api/users/?role=member")
    assert listed.status_code == 200
    assert listed.data[0]["assigned_trainer"]["id"] == trainer.pk

    reassigned = client.put(
        assignment_url(member), {"trainer_id": second_trainer.pk}, format="json"
    )
    assert reassigned.status_code == 200
    assert TrainerAssignment.objects.get(member=member).trainer == second_trainer
    assert TrainerAssignment.objects.filter(member=member).count() == 1

    unassigned = client.delete(assignment_url(member))
    assert unassigned.status_code == 204
    assert not TrainerAssignment.objects.filter(member=member).exists()
    assert client.get(assignment_url(member)).data["trainer"] is None


def test_admin_needs_assign_trainers_to_change_assignment():
    gym, owner, admin, trainer, member = setup_team()
    make_assignment(owner, member, trainer)
    StaffPermission.objects.create(user=admin, code="manage_members")
    client = client_for(admin)

    assert client.get(assignment_url(member)).status_code == 200
    assert client.get("/api/trainers/").status_code == 403
    assert (
        client.put(assignment_url(member), {"trainer_id": trainer.pk}, format="json").status_code
        == 403
    )
    assert client.delete(assignment_url(member)).status_code == 403

    StaffPermission.objects.create(user=admin, code="assign_trainers")
    assert client.get("/api/trainers/").status_code == 200
    assert (
        client.put(assignment_url(member), {"trainer_id": trainer.pk}, format="json").status_code
        == 200
    )


@pytest.mark.parametrize("role", [User.Role.TRAINER, User.Role.MEMBER])
def test_trainer_and_member_cannot_assign_trainers(role):
    gym, owner, _, trainer, member = setup_team()
    actor = make_user(f"+23491000000{50 + list(User.Role.values).index(role)}", role, gym)

    response = client_for(actor).put(
        assignment_url(member), {"trainer_id": trainer.pk}, format="json"
    )

    assert response.status_code == 403
    assert not TrainerAssignment.objects.exists()


def test_assignments_are_gym_scoped():
    gym_a, owner_a, _, trainer_a, member_a = setup_team()
    gym_b = Gym.objects.create(name="Other Gym")
    owner_b = make_user("+2349100000101", User.Role.OWNER, gym_b)
    trainer_b = make_user("+2349100000102", User.Role.TRAINER, gym_b)
    member_b = make_user("+2349100000103", User.Role.MEMBER, gym_b)
    client = client_for(owner_a)

    assert (
        client.put(
            assignment_url(member_a), {"trainer_id": trainer_b.pk}, format="json"
        ).status_code
        == 404
    )
    assert (
        client.put(
            assignment_url(member_b), {"trainer_id": trainer_a.pk}, format="json"
        ).status_code
        == 404
    )
    assert not TrainerAssignment.objects.exists()

    make_assignment(owner_b, member_b, trainer_b)
    assert [row["id"] for row in client.get("/api/trainers/").data] == [trainer_a.pk]
    assert client.get(assignment_url(member_b)).status_code == 404
    assert [row["id"] for row in client.get("/api/users/?role=member").data] == [member_a.pk]


def test_trainer_only_sees_and_accesses_assigned_members():
    gym, owner, _, trainer, assigned_member = setup_team()
    other_member = make_user("+2349100000201", User.Role.MEMBER, gym, "Other Member")
    other_trainer = make_user("+2349100000202", User.Role.TRAINER, gym, "Other Trainer")
    make_assignment(owner, assigned_member, trainer)
    make_assignment(owner, other_member, other_trainer)
    client = client_for(trainer)

    members = client.get("/api/me/members/")

    assert members.status_code == 200
    assert [row["id"] for row in members.data] == [assigned_member.pk]
    assert client.get(assignment_url(assigned_member)).status_code == 200
    assert client.get(assignment_url(other_member)).status_code == 404
    assert (
        client.put(
            assignment_url(assigned_member), {"trainer_id": trainer.pk}, format="json"
        ).status_code
        == 403
    )


def test_member_can_only_see_own_trainer():
    gym, owner, _, trainer, member = setup_team()
    other_member = make_user("+2349100000301", User.Role.MEMBER, gym, "Other Member")
    make_assignment(owner, member, trainer)
    client = client_for(member)

    own = client.get(assignment_url(member))
    trainer_view = client.get("/api/me/trainer/")

    assert own.status_code == 200
    assert own.data["trainer"]["id"] == trainer.pk
    assert trainer_view.status_code == 200
    assert trainer_view.data["member"]["id"] == member.pk
    assert trainer_view.data["trainer"]["id"] == trainer.pk
    assert client.get(assignment_url(other_member)).status_code == 404
    assert client.get("/api/me/members/").status_code == 403
    assert client.get("/api/trainers/").status_code == 403


def test_my_members_and_me_trainer_are_role_scoped():
    gym, owner, admin, trainer, member = setup_team()

    assert client_for(admin).get("/api/me/members/").status_code == 403
    assert client_for(member).get("/api/me/members/").status_code == 403
    assert client_for(trainer).get("/api/me/trainer/").status_code == 403
    assert client_for(admin).get("/api/me/trainer/").status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/trainers/", None),
        ("get", "/api/members/1/trainer/", None),
        ("put", "/api/members/1/trainer/", {"trainer_id": 1}),
        ("delete", "/api/members/1/trainer/", None),
        ("get", "/api/me/members/", None),
        ("get", "/api/me/trainer/", None),
    ],
)
def test_assignment_endpoints_require_authentication(method, path, payload):
    response = getattr(APIClient(), method)(path, payload, format="json")

    assert response.status_code == 401


def test_me_includes_name():
    gym, _, _, _, member = setup_team()

    response = client_for(member).get("/api/me/")

    assert response.status_code == 200
    assert response.data["name"] == "Member"
