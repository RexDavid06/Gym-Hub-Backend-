from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from accounts.managers import UserManager
from gyms.models import Branch, Gym


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super admin"
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        TRAINER = "trainer", "Trainer"
        MEMBER = "member", "Member"

    phone = models.CharField(max_length=32, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    gym = models.ForeignKey(
        Gym, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="users"
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.phone

    def clean(self):
        super().clean()
        if self.branch_id and self.gym_id and self.branch.gym_id != self.gym_id:
            from django.core.exceptions import ValidationError

            raise ValidationError({"branch": "The branch must belong to the user's gym."})


class StaffPermission(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_permissions"
    )
    code = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code"], name="unique_staff_permission_per_user"
            )
        ]

    def __str__(self):
        return f"{self.user}: {self.code}"
