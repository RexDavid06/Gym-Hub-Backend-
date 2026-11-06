from datetime import date, timedelta
from decimal import Decimal
from urllib.error import HTTPError

import pytest
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import StaffPermission, User
from gyms.models import Gym
from memberships.models import (
    MembershipPayment,
    MembershipPlan,
    MembershipSubscription,
    end_date_for,
)
from notifications.models import Notification, NotificationPreference, NotificationSetting
from notifications.providers import (
    MetaWhatsAppProvider,
    PermanentProviderError,
    TermiiSMSProvider,
    TransientProviderError,
)
from notifications.services import queue_membership_reminders
from notifications.tasks import deliver_notification

pytestmark = pytest.mark.django_db


def make_user(phone, role, gym, name="Test User", is_active=True):
    return User.objects.create_user(
        phone=phone,
        password="password123",
        role=role,
        gym=gym,
        name=name,
        is_active=is_active,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def setup_people(prefix="100"):
    gym = Gym.objects.create(name="Gym")
    owner = make_user(f"+234{prefix}0000001", User.Role.OWNER, gym, "Owner")
    admin = make_user(f"+234{prefix}0000002", User.Role.ADMIN, gym, "Admin")
    trainer = make_user(f"+234{prefix}0000003", User.Role.TRAINER, gym, "Trainer")
    member = make_user(f"+234{prefix}0000004", User.Role.MEMBER, gym, "Member")
    other_member = make_user(f"+234{prefix}0000005", User.Role.MEMBER, gym, "Other Member")
    return gym, owner, admin, trainer, member, other_member


def make_plan(gym, name="Monthly", price="15000.00", duration=30):
    return MembershipPlan.objects.create(
        gym=gym, name=name, price=Decimal(price), duration_days=duration
    )


def make_subscription(gym, member, plan, owner, *, start_date=None, end_date=None, status=None):
    start_date = start_date or timezone.localdate()
    end_date = end_date or end_date_for(start_date, plan.duration_days)
    return MembershipSubscription.objects.create(
        gym=gym,
        member=member,
        plan=plan,
        status=status or MembershipSubscription.Status.ACTIVE,
        start_date=start_date,
        end_date=end_date,
        price=plan.price,
        created_by=owner,
    )


def make_notification(
    gym, member, *, event="membership_expiring", schedule_key="7d", channel="sms"
):
    subscription = MembershipSubscription.objects.filter(gym=gym, member=member).first()
    return Notification.objects.create(
        gym=gym,
        recipient=member,
        recipient_phone=member.phone,
        event=event,
        schedule_key=schedule_key,
        channel=channel,
        dedupe_key=f"manual:{gym.pk}:{member.pk}:{event}:{schedule_key}:{channel}",
        body="Test notification",
        payload={
            "subscription_id": subscription.pk if subscription else None,
            "member_id": member.pk,
            "end_date": subscription.end_date.isoformat() if subscription else "",
            "template_parameters": ["Member", "Monthly", gym.name, "01 Jan 2027"],
        },
    )


def test_notification_settings_default_off_and_are_owner_only():
    gym, owner, admin, _, _, _ = setup_people("101")

    response = client_for(owner).get("/api/notification-settings/")

    assert response.status_code == 200
    assert response.data["sms_reminders_enabled"] is False
    assert response.data["sms_receipts_enabled"] is False
    assert response.data["whatsapp_reminders_enabled"] is False
    assert response.data["whatsapp_receipts_enabled"] is False
    assert response.data["sms_available"] is True
    assert response.data["whatsapp_available"] is True
    assert not NotificationSetting.objects.filter(gym=gym).exists()
    assert client_for(admin).get("/api/notification-settings/").status_code == 403

    updated = client_for(owner).patch(
        "/api/notification-settings/", {"sms_reminders_enabled": True}, format="json"
    )

    assert updated.status_code == 200
    assert updated.data["sms_reminders_enabled"] is True
    assert NotificationSetting.objects.get(gym=gym).sms_reminders_enabled is True


def test_unavailable_channel_cannot_be_enabled():
    _, owner, _, _, _, _ = setup_people("102")
    client = client_for(owner)

    with override_settings(SMS_PROVIDER="disabled"):
        response = client.patch(
            "/api/notification-settings/", {"sms_receipts_enabled": True}, format="json"
        )

    assert response.status_code == 400
    assert "sms_reminders_enabled" in response.data
    assert not NotificationSetting.objects.exists()


def test_whatsapp_preference_permissions_and_defaults():
    gym, owner, admin, trainer, member, _ = setup_people("103")
    StaffPermission.objects.create(user=admin, code="manage_members")
    url = f"/api/users/{member.pk}/notification-preference/"

    assert client_for(owner).get(url).data == {"whatsapp_opt_in": False}
    assert client_for(admin).get(url).status_code == 200
    assert client_for(trainer).get(url).status_code == 403
    assert client_for(member).get(url).status_code == 403

    updated = client_for(owner).put(url, {"whatsapp_opt_in": True}, format="json")
    assert updated.status_code == 200
    assert updated.data == {"whatsapp_opt_in": True}
    assert NotificationPreference.objects.get(gym=gym, user=member).whatsapp_opt_in is True

    non_member = client_for(owner).get(f"/api/users/{trainer.pk}/notification-preference/")
    assert non_member.status_code == 400

    other_gym = Gym.objects.create(name="Other Gym")
    other_member = make_user("+2341030000099", User.Role.MEMBER, other_gym, "Other Member")
    cross_gym = client_for(owner).get(f"/api/users/{other_member.pk}/notification-preference/")
    assert cross_gym.status_code == 404


def test_user_list_and_me_expose_whatsapp_opt_in():
    gym, owner, _, _, member, _ = setup_people("104")
    NotificationPreference.objects.create(gym=gym, user=member, whatsapp_opt_in=True)

    listed = client_for(owner).get("/api/users/?role=member")
    me = client_for(member).get("/api/me/")

    assert listed.status_code == 200
    assert listed.data[0]["whatsapp_opt_in"] is True
    assert me.status_code == 200
    assert me.data["whatsapp_opt_in"] is True


def test_payment_receipt_is_queued_after_commit_for_both_channels(
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    gym, owner, _, _, member, _ = setup_people("105")
    plan = make_plan(gym)
    subscription = make_subscription(gym, member, plan, owner)
    NotificationSetting.objects.create(
        gym=gym, sms_receipts_enabled=True, whatsapp_receipts_enabled=True
    )
    NotificationPreference.objects.create(gym=gym, user=member, whatsapp_opt_in=True)

    # No Redis broker is available in this environment, so patch all async
    # delivery tasks to run inline rather than be published to the broker.
    # This mirrors the pattern used throughout the Phase 8 suite
    # (monkeypatch.setattr(deliver_notification, "delay", lambda ...)) and
    # verifies the same delivery code path without a broker.
    monkeypatch.setattr(
        "notifications.tasks.deliver_notification.delay",
        lambda *args, **kwargs: deliver_notification.run(*args, **kwargs),
    )
    monkeypatch.setattr(
        "notifications.tasks.send_payment_receipt.delay",
        lambda *args, **kwargs: _sync_send_payment_receipt(*args, **kwargs),
    )
    monkeypatch.setattr(
        "notifications.tasks.schedule_membership_reminders.delay",
        lambda *args, **kwargs: _sync_schedule_membership_reminders(*args, **kwargs),
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(owner).post(
            "/api/membership-payments/",
            {
                "member_id": member.pk,
                "subscription_id": subscription.pk,
                "amount": "15000.00",
                "method": "cash",
            },
            format="json",
        )

    assert response.status_code == 201
    notifications = Notification.objects.filter(
        gym=gym, event=Notification.Event.PAYMENT_RECEIPT
    ).order_by("channel")
    assert [row.channel for row in notifications] == ["sms", "whatsapp"]
    assert [row.status for row in notifications] == [
        Notification.Status.SENT,
        Notification.Status.SENT,
    ]
    assert all(row.provider == "console" for row in notifications)
    assert all(row.provider_message_id for row in notifications)
    assert notifications[0].body.startswith("Hi Member")
    assert notifications[0].payload["payment_id"] == response.data["id"]


def test_voided_payment_is_skipped_at_delivery():
    gym, owner, _, _, member, _ = setup_people("106")
    plan = make_plan(gym)
    subscription = make_subscription(gym, member, plan, owner)
    NotificationSetting.objects.create(gym=gym, sms_receipts_enabled=True)
    payment = MembershipPayment.objects.create(
        gym=gym,
        member=member,
        subscription=subscription,
        amount=Decimal("15000.00"),
        recorded_by=owner,
    )
    notification = Notification.objects.create(
        gym=gym,
        recipient=member,
        recipient_phone=member.phone,
        event=Notification.Event.PAYMENT_RECEIPT,
        schedule_key=Notification.ScheduleKey.RECEIPT,
        channel=Notification.Channel.SMS,
        dedupe_key=f"payment_receipt:{payment.pk}:sms",
        payload={"payment_id": payment.pk, "member_id": member.pk},
    )
    payment.void(by=owner, reason="Wrong member")

    deliver_notification.run(notification.pk)

    notification.refresh_from_db()
    assert notification.status == Notification.Status.SKIPPED
    assert notification.skip_reason == "payment_voided"


def test_all_reminder_points_and_dedupe(monkeypatch):
    gym, owner, _, _, _, _ = setup_people("107")
    plan = make_plan(gym)
    NotificationSetting.objects.create(gym=gym, sms_reminders_enabled=True)
    monkeypatch.setattr(deliver_notification, "delay", lambda *args, **kwargs: None)
    today = date(2026, 5, 15)

    points = [
        ("3m", relativedelta(months=3), MembershipSubscription.Status.ACTIVE),
        ("1m", relativedelta(months=1), MembershipSubscription.Status.ACTIVE),
        ("7d", relativedelta(days=7), MembershipSubscription.Status.ACTIVE),
        ("3d", relativedelta(days=3), MembershipSubscription.Status.ACTIVE),
        ("due", relativedelta(), MembershipSubscription.Status.ACTIVE),
        ("7d_after", relativedelta(days=-7), MembershipSubscription.Status.EXPIRED),
    ]
    for index, (schedule_key, offset, status) in enumerate(points):
        member = make_user(f"+23410700001{index:02d}", User.Role.MEMBER, gym, f"Member {index}")
        subscription = make_subscription(
            gym,
            member,
            plan,
            owner,
            start_date=today - timedelta(days=60),
            end_date=today + offset,
            status=status,
        )

        created = queue_membership_reminders(subscription, today)
        repeated = queue_membership_reminders(subscription, today)

        assert len(created) == 1
        assert repeated == []
        assert created[0].schedule_key == schedule_key
        assert created[0].status == Notification.Status.PENDING


def test_reminder_before_start_date_is_skipped(monkeypatch):
    gym, owner, _, _, member, _ = setup_people("108")
    plan = make_plan(gym)
    NotificationSetting.objects.create(gym=gym, sms_reminders_enabled=True)
    monkeypatch.setattr(deliver_notification, "delay", lambda *args, **kwargs: None)
    today = date(2026, 5, 15)
    subscription = make_subscription(
        gym,
        member,
        plan,
        owner,
        start_date=today + timedelta(days=1),
        end_date=today + relativedelta(months=3),
    )

    assert queue_membership_reminders(subscription, today) == []
    assert not Notification.objects.exists()


def test_reminders_skip_suspended_gyms_inactive_members_and_unconsented_whatsapp(monkeypatch):
    gym, owner, _, _, member, _ = setup_people("109")
    plan = make_plan(gym)
    setting = NotificationSetting.objects.create(gym=gym, whatsapp_reminders_enabled=True)
    monkeypatch.setattr(deliver_notification, "delay", lambda *args, **kwargs: None)
    today = date(2026, 5, 15)
    subscription = make_subscription(
        gym,
        member,
        plan,
        owner,
        start_date=today - timedelta(days=60),
        end_date=today + timedelta(days=7),
    )

    assert queue_membership_reminders(subscription, today) == []

    NotificationPreference.objects.create(gym=gym, user=member, whatsapp_opt_in=True)
    member.is_active = False
    member.save(update_fields=["is_active"])
    assert queue_membership_reminders(subscription, today) == []

    member.is_active = True
    member.save(update_fields=["is_active"])
    gym.is_active = False
    gym.save(update_fields=["is_active"])
    assert queue_membership_reminders(subscription, today) == []

    gym.is_active = True
    gym.save(update_fields=["is_active"])
    setting.whatsapp_reminders_enabled = True
    setting.save(update_fields=["whatsapp_reminders_enabled"])
    assert len(queue_membership_reminders(subscription, today)) == 1


def test_transient_failure_retries_and_permanent_failure_does_not(monkeypatch):
    gym, owner, _, _, member, _ = setup_people("110")
    plan = make_plan(gym)
    make_subscription(
        gym,
        member,
        plan,
        owner,
        end_date=timezone.localdate() + timedelta(days=7),
    )
    NotificationSetting.objects.create(gym=gym, sms_reminders_enabled=True)
    notification = make_notification(gym, member)
    retries = []

    class TransientProvider:
        name = "fake"

        def send(self, **kwargs):
            raise TransientProviderError("Provider timed out.")

    monkeypatch.setattr("notifications.tasks.get_provider", lambda channel: TransientProvider())
    monkeypatch.setattr(
        deliver_notification,
        "apply_async",
        lambda *args, **kwargs: retries.append(kwargs),
    )

    deliver_notification.run(notification.pk)

    notification.refresh_from_db()
    assert notification.status == Notification.Status.FAILED
    assert notification.attempts == 1
    assert notification.last_error == "Provider timed out."
    assert len(retries) == 1

    class PermanentProvider:
        name = "fake"

        def send(self, **kwargs):
            raise PermanentProviderError("Phone number rejected.")

    monkeypatch.setattr("notifications.tasks.get_provider", lambda channel: PermanentProvider())
    deliver_notification.run(notification.pk)

    notification.refresh_from_db()
    assert notification.status == Notification.Status.FAILED
    assert notification.attempts == 2
    assert notification.last_error == "Phone number rejected."
    assert len(retries) == 1


def test_manual_retry_requires_owner_and_failed_status(monkeypatch):
    gym, owner, admin, _, member, _ = setup_people("111")
    plan = make_plan(gym)
    make_subscription(gym, member, plan, owner)
    NotificationSetting.objects.create(gym=gym, sms_reminders_enabled=True)
    notification = make_notification(gym, member)
    notification.status = Notification.Status.FAILED
    notification.save(update_fields=["status"])
    url = f"/api/notifications/{notification.pk}/retry/"

    # No Redis broker is available in this environment, so patch the async
    # delivery task to run inline. This mirrors the pattern used throughout
    # the Phase 8 suite, and verifies the same retry + delivery code path
    # without a broker.
    monkeypatch.setattr(
        "notifications.tasks.deliver_notification.delay",
        lambda *args, **kwargs: deliver_notification.run(*args, **kwargs),
    )

    assert client_for(admin).post(url).status_code == 403
    response = client_for(owner).post(url)

    assert response.status_code == 202
    notification.refresh_from_db()
    assert notification.status == Notification.Status.SENT

    again = client_for(owner).post(url)
    assert again.status_code == 400


def test_notification_history_is_scoped_filtered_and_permissioned():
    gym, owner, admin, trainer, member, _ = setup_people("112")
    plan = make_plan(gym)
    make_subscription(gym, member, plan, owner)
    own = make_notification(gym, member)
    failed = make_notification(gym, member, schedule_key="3d", channel="whatsapp")
    failed.status = Notification.Status.FAILED
    failed.save(update_fields=["status"])
    other_gym = Gym.objects.create(name="Other Gym")
    other_owner = make_user("+2341120000098", User.Role.OWNER, other_gym, "Other Owner")
    other_member = make_user("+2341120000099", User.Role.MEMBER, other_gym, "Other Member")
    make_notification(other_gym, other_member)

    listed = client_for(owner).get("/api/notifications/")
    filtered = client_for(admin).get("/api/notifications/?status=failed")
    invalid = client_for(owner).get("/api/notifications/?status=unknown")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.data["results"]] == [failed.pk, own.pk]
    assert [row["id"] for row in filtered.data["results"]] == [failed.pk]
    assert invalid.data["results"] == []
    assert client_for(trainer).get("/api/notifications/").status_code == 403
    assert client_for(member).get("/api/notifications/").status_code == 403
    assert client_for(owner).get(f"/api/notifications/{own.pk}/").status_code == 200
    assert client_for(other_owner).get(f"/api/notifications/{own.pk}/").status_code == 404
    assert APIClient().get("/api/notifications/").status_code == 401


def _sync_send_payment_receipt(payment_id, gym_id=None):
    # Run the sync send-payment-receipt logic inline, without a broker, so
    # receipt-delivery tests do not require a live Redis/Celery broker.
    from notifications.tasks import send_payment_receipt as sync_send

    return sync_send(payment_id, gym_id)


def _sync_schedule_membership_reminders():
    # Run the schedule-membership-reminders logic inline, without a broker,
    # so reminder-scheduling tests do not require a live Redis/Celery broker.
    from notifications.tasks import schedule_membership_reminders as sync_schedule

    return sync_schedule()


def test_celery_configuration():
    assert settings.CELERY_TIMEZONE == "Africa/Lagos"
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert settings.CELERY_TASK_DEFAULT_QUEUE == "default"
    assert (
        settings.CELERY_TASK_ROUTES["notifications.tasks.deliver_notification"]["queue"]
        == "notifications"
    )
    beat = settings.CELERY_BEAT_SCHEDULE["schedule-membership-reminders"]
    assert beat["task"] == "notifications.tasks.schedule_membership_reminders"
    assert beat["schedule"].hour == {8}


def _http_error(code):
    return HTTPError("https://provider.example", code, "error", hdrs=None, fp=None)


@override_settings(TERMII_API_KEY="key", TERMII_SENDER_ID="GymHub")
def test_termii_maps_provider_http_errors(monkeypatch):
    provider = TermiiSMSProvider()

    monkeypatch.setattr(
        "notifications.providers.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(_http_error(429)),
    )
    with pytest.raises(TransientProviderError):
        provider.send(to="+2348000000000", message="hello", idempotency_key="key")

    monkeypatch.setattr(
        "notifications.providers.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(_http_error(400)),
    )
    with pytest.raises(PermanentProviderError):
        provider.send(to="+2348000000000", message="hello", idempotency_key="key")


@override_settings(WHATSAPP_ACCESS_TOKEN="token", WHATSAPP_PHONE_NUMBER_ID="123")
def test_meta_whatsapp_sends_an_approved_template(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"messages":[{"id":"wamid.123"}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data.decode()
        return Response()

    monkeypatch.setattr("notifications.providers.urllib.request.urlopen", fake_urlopen)

    message_id = MetaWhatsAppProvider().send(
        to="+2348000000000",
        template_name="membership_reminder",
        language="en_US",
        parameters=["Ada", "Monthly", "Gym", "01 Jan 2027"],
        idempotency_key="key",
    )

    assert message_id == "wamid.123"
    assert captured["url"] == "https://graph.facebook.com/v20.0/123/messages"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert '"name": "membership_reminder"' in captured["body"]
    assert '"parameters"' in captured["body"]
