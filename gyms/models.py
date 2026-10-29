from django.db import models


class Gym(models.Model):
    class PlanTier(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        BIG = "big", "Big"

    name = models.CharField(max_length=200)
    plan_tier = models.CharField(max_length=10, choices=PlanTier.choices, default=PlanTier.SMALL)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Branch(models.Model):
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gym", "name"], name="unique_branch_name_per_gym")
        ]

    def __str__(self):
        return f"{self.name} ({self.gym})"
