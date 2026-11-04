from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Lightweight readiness probe: confirms the database answers a query."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        responses={
            200: {"type": "object", "properties": {"status": {"type": "string"}}},
            503: {"type": "object", "properties": {"status": {"type": "string"}}},
        }
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:  # noqa: BLE001 - never expose failure details
            return Response({"status": "unavailable"}, status=503)
        return Response({"status": "ok"})
