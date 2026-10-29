from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from accounts.views import (
    AdminListView,
    AdminPermissionRevokeView,
    AdminPermissionsView,
    DeactivateUserView,
    InviteView,
    MeView,
    PhoneTokenObtainPairView,
    PhoneTokenRefreshView,
    RedeemInviteView,
    RegisterGymView,
    UserListViewSet,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/token/", PhoneTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", PhoneTokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/register-gym/", RegisterGymView.as_view(), name="register_gym"),
    path("api/auth/redeem-invite/", RedeemInviteView.as_view(), name="redeem_invite"),
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
    path("api/users/", UserListViewSet.as_view({"get": "list"}), name="user_list"),
    path(
        "api/users/<int:user_id>/deactivate/",
        DeactivateUserView.as_view(),
        name="user_deactivate",
    ),
    path("api/me/", MeView.as_view(), name="me"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
