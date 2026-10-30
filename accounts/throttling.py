"""Throttles that key on something other than the authenticated user."""

from rest_framework.throttling import SimpleRateThrottle


class PhoneLoginThrottle(SimpleRateThrottle):
    """Limit login attempts per submitted phone number.

    Keyed on the phone in the request body so it holds no matter where the
    attempt comes from: a rate-limited attacker cycling IP addresses still
    shares one bucket per target number. The scope rate lives in
    ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login_phone"]``.
    """

    scope = "login_phone"

    def get_cache_key(self, request, view):
        phone = str(request.data.get("phone") or "").strip().lower()
        if not phone:
            return None
        return self.cache_format % {"scope": self.scope, "ident": f"phone:{phone}"}
