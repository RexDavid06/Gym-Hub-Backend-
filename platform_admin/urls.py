from django.urls import path

from platform_admin.views import (
    PlatformGymDetailView,
    PlatformGymListView,
    PlatformSummaryView,
)

urlpatterns = [
    path("summary/", PlatformSummaryView.as_view(), name="platform_summary"),
    path("gyms/", PlatformGymListView.as_view(), name="platform_gym_list"),
    path("gyms/<int:pk>/", PlatformGymDetailView.as_view(), name="platform_gym_detail"),
]
