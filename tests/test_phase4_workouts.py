import pytest
from rest_framework.test import APIClient

from accounts.models import StaffPermission, TrainerAssignment, User
from gyms.models import Gym
from workouts.models import WorkoutExercise, WorkoutPlan

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym, name="Test User"):
    return User.objects.create_user(
        phone=phone, password="password123", role=role, gym=gym, name=name
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def setup_team():
    gym = Gym.objects.create(name="Gym")
    owner = make_user("+2349200000001", User.Role.OWNER, gym, "Owner")
    admin = make_user("+2349200000002", User.Role.ADMIN, gym, "Admin")
    trainer = make_user("+2349200000003", User.Role.TRAINER, gym, "Trainer")
    member = make_user("+2349200000004", User.Role.MEMBER, gym, "Member")
    TrainerAssignment.objects.create(gym=gym, member=member, trainer=trainer, assigned_by=owner)
    return gym, owner, admin, trainer, member


def plan_payload(member, title="Strength Plan"):
    return {
        "member_id": member.pk,
        "title": title,
        "notes": "Three days a week.",
        "status": WorkoutPlan.Status.ACTIVE,
        "exercises": [
            {"name": "Squat", "sets": 3, "reps": "8-10", "weight": "40kg", "order": 1},
            {"name": "Bench press", "sets": 3, "reps": "10", "weight": "30kg", "order": 2},
        ],
    }


def test_trainer_creates_plan_with_exercises_for_assigned_member():
    gym, owner, _, trainer, member = setup_team()

    response = client_for(trainer).post("/api/workout-plans/", plan_payload(member), format="json")

    assert response.status_code == 201
    plan = WorkoutPlan.objects.get(pk=response.data["id"])
    assert (plan.gym, plan.member, plan.trainer) == (gym, member, trainer)
    assert response.data["member"]["id"] == member.pk
    assert response.data["trainer"]["id"] == trainer.pk
    assert [exercise["name"] for exercise in response.data["exercises"]] == [
        "Squat",
        "Bench press",
    ]
    assert set(WorkoutExercise.objects.values_list("gym_id", flat=True)) == {gym.pk}
    assert client_for(trainer).get("/api/workout-plans/").data[0]["id"] == plan.pk
    assert client_for(trainer).get(f"/api/workout-plans/{plan.pk}/").status_code == 200


def test_trainer_cannot_create_for_unassigned_or_cross_gym_member():
    gym, _, _, trainer, _ = setup_team()
    unassigned = make_user("+2349200000010", User.Role.MEMBER, gym, "Unassigned")
    other_gym = Gym.objects.create(name="Other Gym")
    other_member = make_user("+2349200000011", User.Role.MEMBER, other_gym, "Other")

    unassigned_response = client_for(trainer).post(
        "/api/workout-plans/", plan_payload(unassigned), format="json"
    )
    cross_gym_response = client_for(trainer).post(
        "/api/workout-plans/", plan_payload(other_member), format="json"
    )

    assert unassigned_response.status_code == 403
    assert cross_gym_response.status_code == 404
    assert not WorkoutPlan.objects.exists()


@pytest.mark.parametrize("role", [User.Role.OWNER, User.Role.ADMIN, User.Role.MEMBER])
def test_only_trainers_can_create_plans(role):
    gym, owner, admin, trainer, member = setup_team()
    actor = {User.Role.OWNER: owner, User.Role.ADMIN: admin, User.Role.MEMBER: member}[role]
    if role == User.Role.ADMIN:
        StaffPermission.objects.create(user=admin, code="view_reports")

    response = client_for(actor).post("/api/workout-plans/", plan_payload(member), format="json")

    assert response.status_code == 403
    assert not WorkoutPlan.objects.exists()


def test_trainer_cannot_access_another_trainers_plan():
    gym, owner, _, trainer, member = setup_team()
    other_trainer = make_user("+2349200000020", User.Role.TRAINER, gym, "Other Trainer")
    plan = WorkoutPlan.objects.create(
        gym=gym, member=member, trainer=other_trainer, title="Other Plan"
    )

    assert client_for(trainer).get("/api/workout-plans/").data == []
    assert client_for(trainer).get(f"/api/workout-plans/{plan.pk}/").status_code == 404
    assert (
        client_for(trainer)
        .patch(f"/api/workout-plans/{plan.pk}/", {"title": "Hacked"}, format="json")
        .status_code
        == 404
    )


def test_member_only_sees_own_plans():
    gym, owner, _, trainer, member = setup_team()
    other_member = make_user("+2349200000030", User.Role.MEMBER, gym, "Other Member")
    TrainerAssignment.objects.create(
        gym=gym, member=other_member, trainer=trainer, assigned_by=owner
    )
    own_plan = WorkoutPlan.objects.create(gym=gym, member=member, trainer=trainer, title="Own Plan")
    other_plan = WorkoutPlan.objects.create(
        gym=gym, member=other_member, trainer=trainer, title="Other Plan"
    )
    client = client_for(member)

    listed = client.get("/api/workout-plans/")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.data] == [own_plan.pk]
    assert client.get(f"/api/workout-plans/{own_plan.pk}/").status_code == 200
    assert client.get(f"/api/workout-plans/{other_plan.pk}/").status_code == 404
    assert (
        client.post("/api/workout-plans/", plan_payload(member), format="json").status_code == 403
    )
    assert (
        client.patch(
            f"/api/workout-plans/{own_plan.pk}/", {"title": "Changed"}, format="json"
        ).status_code
        == 403
    )
    assert client.delete(f"/api/workout-plans/{own_plan.pk}/").status_code == 403


def test_plan_queries_are_gym_scoped_and_read_only_for_owner_and_admin():
    gym, owner, admin, trainer, member = setup_team()
    StaffPermission.objects.create(user=admin, code="view_reports")
    plan = WorkoutPlan.objects.create(gym=gym, member=member, trainer=trainer, title="Gym Plan")
    other_gym = Gym.objects.create(name="Other Gym")
    other_owner = make_user("+2349200000040", User.Role.OWNER, other_gym, "Other Owner")
    other_trainer = make_user("+2349200000041", User.Role.TRAINER, other_gym, "Other Trainer")
    other_member = make_user("+2349200000042", User.Role.MEMBER, other_gym, "Other Member")
    TrainerAssignment.objects.create(
        gym=other_gym, member=other_member, trainer=other_trainer, assigned_by=other_owner
    )
    other_plan = WorkoutPlan.objects.create(
        gym=other_gym, member=other_member, trainer=other_trainer, title="Other Gym Plan"
    )

    owner_list = client_for(owner).get("/api/workout-plans/")
    admin_list = client_for(admin).get("/api/workout-plans/")

    assert [row["id"] for row in owner_list.data] == [plan.pk]
    assert [row["id"] for row in admin_list.data] == [plan.pk]
    assert client_for(owner).get(f"/api/workout-plans/{other_plan.pk}/").status_code == 404
    assert (
        client_for(owner)
        .post("/api/workout-plans/", plan_payload(member), format="json")
        .status_code
        == 403
    )


def test_update_replaces_exercises_and_delete_is_author_only():
    gym, owner, _, trainer, member = setup_team()
    plan = WorkoutPlan.objects.create(gym=gym, member=member, trainer=trainer, title="Old Title")
    WorkoutExercise.objects.create(gym=gym, plan=plan, name="Old Exercise")
    client = client_for(trainer)

    updated = client.patch(
        f"/api/workout-plans/{plan.pk}/",
        {
            "title": "New Title",
            "status": WorkoutPlan.Status.ARCHIVED,
            "exercises": [{"name": "Deadlift", "sets": 4, "reps": "6", "order": 1}],
        },
        format="json",
    )

    assert updated.status_code == 200
    assert updated.data["title"] == "New Title"
    assert updated.data["status"] == WorkoutPlan.Status.ARCHIVED
    assert [exercise["name"] for exercise in updated.data["exercises"]] == ["Deadlift"]
    assert client.delete(f"/api/workout-plans/{plan.pk}/").status_code == 204
    assert not WorkoutPlan.objects.filter(pk=plan.pk).exists()


def test_member_id_cannot_be_changed_on_update():
    gym, owner, _, trainer, member = setup_team()
    other_member = make_user("+2349200000050", User.Role.MEMBER, gym, "Other Member")
    TrainerAssignment.objects.create(
        gym=gym, member=other_member, trainer=trainer, assigned_by=owner
    )
    plan = WorkoutPlan.objects.create(gym=gym, member=member, trainer=trainer, title="Plan")

    response = client_for(trainer).patch(
        f"/api/workout-plans/{plan.pk}/", {"member_id": other_member.pk}, format="json"
    )

    assert response.status_code == 400


def test_unassigning_trainer_revokes_plan_access():
    gym, owner, _, trainer, member = setup_team()
    plan = WorkoutPlan.objects.create(gym=gym, member=member, trainer=trainer, title="Plan")
    TrainerAssignment.objects.filter(member=member).delete()

    assert client_for(trainer).get("/api/workout-plans/").data == []
    assert client_for(trainer).get(f"/api/workout-plans/{plan.pk}/").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/workout-plans/", None),
        ("post", "/api/workout-plans/", {"member_id": 1, "title": "Plan"}),
        ("get", "/api/workout-plans/1/", None),
        ("patch", "/api/workout-plans/1/", {"title": "Plan"}),
        ("delete", "/api/workout-plans/1/", None),
    ],
)
def test_workout_plan_endpoints_require_authentication(method, path, payload):
    response = getattr(APIClient(), method)(path, payload, format="json")

    assert response.status_code == 401
