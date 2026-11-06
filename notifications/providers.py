import json
import logging
import socket
import urllib.error
import urllib.request
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    transient = False


class TransientProviderError(ProviderError):
    transient = True


class PermanentProviderError(ProviderError):
    transient = False


def mask_phone(phone):
    value = str(phone or "")
    if len(value) <= 4:
        return "***"
    return f"{value[:3]}***{value[-2:]}"


def _request_json(request):
    timeout = getattr(settings, "NOTIFICATION_HTTP_TIMEOUT", 10)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 429 or error.code >= 500:
            raise TransientProviderError(f"Provider unavailable (HTTP {error.code}).") from error
        raise PermanentProviderError(
            f"Provider rejected the request (HTTP {error.code})."
        ) from error
    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        raise TransientProviderError("Provider could not be reached.") from error

    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise PermanentProviderError("Provider returned an invalid response.") from error


class ConsoleSMSProvider:
    name = "console"

    def send(self, *, to, message, idempotency_key):
        logger.info("Console SMS to %s (idempotency %s)", mask_phone(to), idempotency_key)
        return f"console-sms-{uuid.uuid4()}"


class TermiiSMSProvider:
    name = "termii"

    def __init__(self):
        self.api_key = settings.TERMII_API_KEY
        self.sender_id = settings.TERMII_SENDER_ID
        self.base_url = settings.TERMII_BASE_URL.rstrip("/")
        self.channel = settings.TERMII_CHANNEL

    def send(self, *, to, message, idempotency_key):
        payload = {
            "api_key": self.api_key,
            "to": to,
            "from": self.sender_id,
            "sms": message,
            "type": "plain",
            "channel": self.channel,
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/sms/send",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        response = _request_json(request)
        if not isinstance(response, dict):
            raise PermanentProviderError("Provider returned an invalid response.")
        return str(response.get("message_id") or response.get("messageId") or "")


class ConsoleWhatsAppProvider:
    name = "console"

    def send(self, *, to, template_name, language, parameters, idempotency_key):
        logger.info(
            "Console WhatsApp to %s using %s/%s (idempotency %s)",
            mask_phone(to),
            template_name,
            language,
            idempotency_key,
        )
        return f"console-whatsapp-{uuid.uuid4()}"


class MetaWhatsAppProvider:
    name = "meta"

    def __init__(self):
        self.access_token = settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.api_version = settings.WHATSAPP_API_VERSION

    def send(self, *, to, template_name, language, parameters, idempotency_key):
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": str(parameter)} for parameter in parameters
                        ],
                    }
                ],
            },
        }
        request = urllib.request.Request(
            (f"https://graph.facebook.com/{self.api_version}/" f"{self.phone_number_id}/messages"),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        response = _request_json(request)
        if not isinstance(response, dict):
            raise PermanentProviderError("Provider returned an invalid response.")
        messages = response.get("messages")
        if not isinstance(messages, list) or not messages:
            raise PermanentProviderError("Provider returned an invalid response.")
        message = messages[0]
        if not isinstance(message, dict):
            raise PermanentProviderError("Provider returned an invalid response.")
        return str(message.get("id") or "")


def sms_available():
    provider = getattr(settings, "SMS_PROVIDER", "console")
    if provider == "console":
        return True
    if provider == "termii":
        return bool(settings.TERMII_API_KEY and settings.TERMII_SENDER_ID)
    return False


def whatsapp_available():
    provider = getattr(settings, "WHATSAPP_PROVIDER", "console")
    if provider == "console":
        return True
    if provider == "meta":
        return bool(
            settings.WHATSAPP_ACCESS_TOKEN
            and settings.WHATSAPP_PHONE_NUMBER_ID
            and settings.WHATSAPP_TEMPLATE_MEMBERSHIP_REMINDER
            and settings.WHATSAPP_TEMPLATE_MEMBERSHIP_EXPIRED
            and settings.WHATSAPP_TEMPLATE_PAYMENT_RECEIPT
        )
    return False


def channel_available(channel):
    if channel == "sms":
        return sms_available()
    if channel == "whatsapp":
        return whatsapp_available()
    return False


def get_sms_provider():
    provider = getattr(settings, "SMS_PROVIDER", "console")
    if provider == "console":
        return ConsoleSMSProvider()
    if provider == "termii":
        return TermiiSMSProvider()
    raise PermanentProviderError("SMS provider is not configured.")


def get_whatsapp_provider():
    provider = getattr(settings, "WHATSAPP_PROVIDER", "console")
    if provider == "console":
        return ConsoleWhatsAppProvider()
    if provider == "meta":
        return MetaWhatsAppProvider()
    raise PermanentProviderError("WhatsApp provider is not configured.")


def get_provider(channel):
    if channel == "sms":
        return get_sms_provider()
    if channel == "whatsapp":
        return get_whatsapp_provider()
    raise PermanentProviderError("Notification channel is not supported.")
