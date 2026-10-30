from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from accounts.tenant import GymOwnedModel


class WorkoutPlan(GymOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workout_plans"
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="authored_workout_plans"
    )
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def clean(self):
        super().clean()
        if self.gym_id and self.member_id and self.member.gym_id != self.gym_id:
            raise ValidationError({"member": "The member must belong to the plan's gym."})
        if self.gym_id and self.trainer_id and self.trainer.gym_id != self.gym_id:
            raise ValidationError({"trainer": "The trainer must belong to the plan's gym."})
        if self.member_id and self.member.role != "member":
            raise ValidationError({"member": "Workout plans can only belong to members."})
        if self.trainer_id and self.trainer.role != "trainer":
            raise ValidationError({"trainer": "Workout plans can only be authored by trainers."})

    def __str__(self):
        return self.title


class WorkoutExercise(GymOwnedModel):
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name="exercises")
    name = models.CharField(max_length=200)
    sets = models.PositiveIntegerField(default=3)
    reps = models.CharField(max_length=50, default="10")
    weight = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def clean(self):
        super().clean()
        if self.plan_id and self.gym_id and self.plan.gym_id != self.gym_id:
            raise ValidationError({"plan": "The exercise must belong to the plan's gym."})

    def __str__(self):
        return self.name
