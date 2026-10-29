from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.serializers import (
    MeSerializer,
    PhoneTokenSerializer,
    TokenPairSerializer,
    TokenRefreshResponseSerializer,
)


class PhoneTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "phone"

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["gym_id"] = user.gym_id
        return token


class PhoneTokenObtainPairView(TokenObtainPairView):
    serializer_class = PhoneTokenObtainPairSerializer
    permission_classes = [AllowAny]

    @extend_schema(request=PhoneTokenSerializer, responses=TokenPairSerializer)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class PhoneTokenRefreshView(TokenRefreshView):
    @extend_schema(responses=TokenRefreshResponseSerializer)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user).data, status=status.HTTP_200_OK)
