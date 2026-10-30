from django.db import transaction
from rest_framework import serializers

from accounts.serializers import UserSummarySerializer
from workouts.models import WorkoutExercise, WorkoutPlan


class WorkoutExerciseSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkoutExercise
        fields = ["id", "name", "sets", "reps", "weight", "notes", "order"]


class WorkoutPlanSerializer(serializers.ModelSerializer):
    member = UserSummarySerializer(read_only=True)
    trainer = UserSummarySerializer(read_only=True)
    member_id = serializers.IntegerField(write_only=True, required=False)
    exercises = WorkoutExerciseSerializer(many=True, required=False)

    class Meta:
        model = WorkoutPlan
        fields = [
            "id",
            "member",
            "member_id",
            "trainer",
            "title",
            "notes",
            "status",
            "exercises",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        exercises = validated_data.pop("exercises", [])
        validated_data.pop("member_id", None)
        gym = validated_data.pop("gym")
        with transaction.atomic():
            plan = WorkoutPlan.objects.create(gym=gym, **validated_data)
            WorkoutExercise.objects.bulk_create(
                [WorkoutExercise(gym=gym, plan=plan, **exercise) for exercise in exercises]
            )
        return plan

    def update(self, instance, validated_data):
        exercises = validated_data.pop("exercises", None)
        validated_data.pop("member_id", None)
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            if exercises is not None:
                instance.exercises.all().delete()
                WorkoutExercise.objects.bulk_create(
                    [
                        WorkoutExercise(gym=instance.gym, plan=instance, **exercise)
                        for exercise in exercises
                    ]
                )
        return instance
