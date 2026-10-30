from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from accounts.views import (
    AdminListView,
    AdminPermissionRevokeView,
    AdminPermissionsView,
    DeactivateUserView,
    InviteView,
    LogoutView,
    MemberTrainerView,
    MeView,
    MyMembersView,
    MyTrainerView,
    PhoneTokenObtainPairView,
    PhoneTokenRefreshView,
    RedeemInviteView,
    RegisterGymView,
    TrainerListView,
    UserListViewSet,
)
from memberships.views import (
    MembershipPaymentViewSet,
    MembershipPlanViewSet,
    MembershipSubscriptionViewSet,
    MyMembershipView,
)
from workouts.views import WorkoutPlanViewSet

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/token/", PhoneTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", PhoneTokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/register-gym/", RegisterGymView.as_view(), name="register_gym"),
    path("api/auth/redeem-invite/", RedeemInviteView.as_view(), name="redeem_invite"),
    path("api/auth/logout/", LogoutView.as_view(), name="logout"),
    path("api/invites/", InviteView.as_view(), name="invite_create"),
    path("api/admins/", AdminListView.as_view(), name="admin_list"),
    path(
        "api/admins/<int:user_id>/permissions/",
        AdminPermissionsView.as_view(),
        name="admin_permissions_set",
    ),
    path(
        "api/admins/<int:user_id>/permissions/<str:code>/",
        AdminPermissionRevokeView.as_view(),
        name="admin_permission_revoke",
    ),
    path("api/trainers/", TrainerListView.as_view(), name="trainer_list"),
    path(
        "api/members/<int:user_id>/trainer/",
        MemberTrainerView.as_view(),
        name="member_trainer",
    ),
    path("api/me/members/", MyMembersView.as_view(), name="my_members"),
    path("api/me/trainer/", MyTrainerView.as_view(), name="my_trainer"),
    path("api/users/", UserListViewSet.as_view({"get": "list"}), name="user_list"),
    path(
        "api/users/<int:user_id>/deactivate/",
        DeactivateUserView.as_view(),
        name="user_deactivate",
    ),
    path(
        "api/workout-plans/",
        WorkoutPlanViewSet.as_view({"get": "list", "post": "create"}),
        name="workout_plan_list",
    ),
    path(
        "api/workout-plans/<int:pk>/",
        WorkoutPlanViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="workout_plan_detail",
    ),
    path(
        "api/membership-plans/",
        MembershipPlanViewSet.as_view({"get": "list", "post": "create"}),
        name="membership_plan_list",
    ),
    path(
        "api/membership-plans/<int:pk>/",
        MembershipPlanViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="membership_plan_detail",
    ),
    path(
        "api/memberships/",
        MembershipSubscriptionViewSet.as_view({"get": "list", "post": "create"}),
        name="membership_list",
    ),
    path(
        "api/memberships/<int:pk>/",
        MembershipSubscriptionViewSet.as_view({"get": "retrieve"}),
        name="membership_detail",
    ),
    path(
        "api/memberships/<int:pk>/renew/",
        MembershipSubscriptionViewSet.as_view({"post": "renew"}),
        name="membership_renew",
    ),
    path(
        "api/memberships/<int:pk>/cancel/",
        MembershipSubscriptionViewSet.as_view({"post": "cancel"}),
        name="membership_cancel",
    ),
    path(
        "api/membership-payments/",
        MembershipPaymentViewSet.as_view({"get": "list", "post": "create"}),
        name="membership_payment_list",
    ),
    path(
        "api/membership-payments/<int:pk>/",
        MembershipPaymentViewSet.as_view({"get": "retrieve"}),
        name="membership_payment_detail",
    ),
    path(
        "api/membership-payments/<int:pk>/void/",
        MembershipPaymentViewSet.as_view({"post": "void"}),
        name="membership_payment_void",
    ),
    path("api/me/membership/", MyMembershipView.as_view(), name="my_membership"),
    path("api/me/", MeView.as_view(), name="me"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
