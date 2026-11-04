from drf_spectacular.extensions import OpenApiAuthenticationExtension


class GymAwareJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describe the gym-aware JWT authenticator in the OpenAPI schema."""

    target_class = "accounts.authentication.GymAwareJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
