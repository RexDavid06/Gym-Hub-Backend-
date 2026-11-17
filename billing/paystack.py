"""Minimal Paystack client for Phase 10 (config-gated).

No secret is required to import or test this module: everything checks the
``BILLING_PROVIDER``/``PAYSTACK_*`` settings at call time. When the provider is
disabled the checkout path is never reached and the platform runs on manual
payment recording.
"""

import hashlib
import hmac
import json
import socket
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    pass


class PaystackTransientError(PaystackError):
    pass


def provider_enabled():
    return getattr(settings, "BILLING_PROVIDER", "manual") == "paystack"


def _secret_key():
    return getattr(settings, "PAYSTACK_SECRET_KEY", "")


def amount_to_kobo(amount):
    return int((Decimal(amount) * 100).to_integral_value())


def _request_json(request):
    timeout = getattr(settings, "BILLING_HTTP_TIMEOUT", 15)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 429 or error.code >= 500:
            raise PaystackTransientError(f"Paystack unavailable (HTTP {error.code}).") from error
        raise PaystackError(f"Paystack rejected the request (HTTP {error.code}).") from error
    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        raise PaystackTransientError("Paystack could not be reached.") from error
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise PaystackError("Paystack returned an invalid response.") from error


def _auth_headers():
    return {
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
    }


def billing_email(gym_id):
    domain = getattr(settings, "PAYSTACK_EMAIL_DOMAIN", "gymhub.ng")
    return f"billing+gym{gym_id}@{domain}"


def initialize_transaction(*, email, amount, reference, callback_url="", metadata=None):
    """Create a hosted checkout transaction. Returns ``{authorization_url, reference}``."""
    payload = {
        "email": email,
        "amount": amount_to_kobo(amount),
        "reference": reference,
        "metadata": metadata or {},
    }
    if callback_url:
        payload["callback_url"] = callback_url
    request = urllib.request.Request(
        f"{PAYSTACK_BASE_URL}/transaction/initialize",
        data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(),
        method="POST",
    )
    response = _request_json(request)
    data = response.get("data") if isinstance(response, dict) else None
    if not response.get("status") or not isinstance(data, dict):
        raise PaystackError("Paystack did not return a checkout URL.")
    return {
        "authorization_url": data.get("authorization_url", ""),
        "access_code": data.get("access_code", ""),
        "reference": data.get("reference", reference),
    }


def verify_transaction(reference):
    """Verify a transaction. Returns the transaction ``data`` dict."""
    request = urllib.request.Request(
        f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
        headers=_auth_headers(),
        method="GET",
    )
    response = _request_json(request)
    data = response.get("data") if isinstance(response, dict) else None
    if not response.get("status") or not isinstance(data, dict):
        raise PaystackError("Paystack could not verify the transaction.")
    return data


def verify_webhook_signature(body, signature):
    """Verify the ``x-paystack-signature`` HMAC-SHA512 of the raw request body."""
    secret = _secret_key()
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
