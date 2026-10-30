from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from accounts.managers import UserManager
from accounts.tenant import GymFilterableManager
from gyms.models import Branch, Gym


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super admin"
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        TRAINER = "trainer", "Trainer"
        MEMBER = "member", "Member"

    name = models.CharField(max_length=200, blank=True)
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


class TrainerAssignment(models.Model):
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="trainer_assignments")
    member = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trainer_assignment"
    )
    trainer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="member_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="trainer_assignments_made"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GymFilterableManager()

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        if self.gym_id and self.member_id and self.member.gym_id != self.gym_id:
            raise ValidationError({"member": "The member must belong to the assignment's gym."})
        if self.gym_id and self.trainer_id and self.trainer.gym_id != self.gym_id:
            raise ValidationError({"trainer": "The trainer must belong to the assignment's gym."})
        if self.member_id and self.member.role != User.Role.MEMBER:
            raise ValidationError({"member": "Only members can be assigned to a trainer."})
        if self.trainer_id and self.trainer.role != User.Role.TRAINER:
            raise ValidationError({"trainer": "Only trainers can be assigned to members."})

    def __str__(self):
        return f"{self.member} -> {self.trainer}"


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


def invite_expiry_default():
    return timezone.now() + timedelta(days=7)


class Invite(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        TRAINER = "trainer", "Trainer"
        MEMBER = "member", "Member"

    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, related_name="invites")
    role = models.CharField(max_length=20, choices=Role.choices)
    phone = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_invites"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(default=invite_expiry_default)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = GymFilterableManager()

    def __str__(self):
        return f"{self.role} invite for {self.phone}"


# ENUM_NAME_OVERRIDES resolves a dotted path to a single attribute of an
# importable module, so the choice sets are re-exported from here to keep the
# OpenAPI role enums from colliding (see SPECTACULAR_SETTINGS).
USER_ROLE_CHOICES = User.Role.choices
INVITE_ROLE_CHOICES = Invite.Role.choices
