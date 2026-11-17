import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from billing import services
from billing.models import (
    BillingPayment,
    BillingPlan,
    BillingPlanPrice,
    DunningAttempt,
    Invoice,
    WebhookEvent,
)
from billing.tasks import generate_billing_invoices, run_billing_dunning
from gyms.models import Gym

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Passw0rd!42"


def make_user(phone, role, gym=None, name="Test User", is_active=True):
    return User.objects.create_user(
        phone=phone, password=PASSWORD, role=role, gym=gym, name=name, is_active=is_active
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_plan(tier="small", name="Starter", **kwargs):
    return BillingPlan.objects.create(name=name, tier=tier, **kwargs)


def add_price(plan, interval="monthly", amount="10000.00", effective_from=None, **kwargs):
    return BillingPlanPrice.objects.create(
        plan=plan,
        interval=interval,
        amount=Decimal(amount),
        effective_from=effective_from or timezone.localdate(),
        **kwargs,
    )


@pytest.fixture
def super_admin():
    return make_user("+2347000000001", User.Role.SUPER_ADMIN, name="Platform")


@pytest.fixture
def gym_people():
    gym = Gym.objects.create(name="Alpha Fitness", plan_tier="small")
    owner = make_user("+2347000000101", User.Role.OWNER, gym, "Alpha Owner")
    admin = make_user("+2347000000102", User.Role.ADMIN, gym, "Alpha Admin")
    trainer = make_user("+2347000000103", User.Role.TRAINER, gym, "Alpha Trainer")
    member = make_user("+2347000000104", User.Role.MEMBER, gym, "Alpha Member")
    return gym, owner, admin, trainer, member


def subscribed_gym(name, phone, tier="small", amount="10000.00", interval="monthly", start=None):
    gym = Gym.objects.create(name=name, plan_tier=tier)
    owner = make_user(phone, User.Role.OWNER, gym, f"{name} Owner")
    plan = make_plan(tier=tier, name=f"{name} Plan")
    price = add_price(plan, interval=interval, amount=amount)
    subscription = services.create_subscription(
        gym=gym, plan=plan, price=price, created_by=owner, start=start
    )
    return gym, owner, subscription


def test_platform_billing_endpoints_reject_non_super_admin(gym_people, super_admin):
    gym, owner, admin, trainer, member = gym_people
    paths = [
        ("get", "/api/platform/billing/plans/"),
        ("get", "/api/platform/billing/subscriptions/"),
        ("get", "/api/platform/billing/invoices/"),
        ("get", "/api/platform/billing/payments/"),
        ("get", "/api/platform/billing/dunning/"),
        ("get", "/api/platform/billing/summary/"),
    ]
    for method, path in paths:
        assert getattr(APIClient(), method)(path).status_code == 401
        for user in (owner, admin, trainer, member):
            assert getattr(client_for(user), method)(path).status_code == 403, (path, user.role)
        assert getattr(client_for(super_admin), method)(path).status_code == 200


def test_owner_billing_endpoints_reject_non_owner(gym_people, super_admin):
    gym, owner, admin, trainer, member = gym_people
    paths = [
        ("get", "/api/billing/subscription/"),
        ("get", "/api/billing/invoices/"),
        ("get", "/api/billing/payments/"),
    ]
    for method, path in paths:
        assert getattr(APIClient(), method)(path).status_code == 401
        for user in (admin, trainer, member, super_admin):
            assert getattr(client_for(user), method)(path).status_code == 403, (path, user.role)


def test_price_versioning_uses_effective_date():
    plan = make_plan()
    today = timezone.localdate()
    first = add_price(plan, amount="10000.00", effective_from=today)
    second = add_price(plan, amount="12000.00", effective_from=today + timedelta(days=30))

    assert plan.current_price("monthly", on=today).pk == first.pk
    assert plan.current_price("monthly", on=today + timedelta(days=31)).pk == second.pk
    assert plan.current_price("annual", on=today) is None


def test_platform_creates_plan_with_price_and_subscription(super_admin):
    client = client_for(super_admin)
    response = client.post(
        "/api/platform/billing/plans/",
        {"name": "Pro", "tier": "medium", "description": "For growing gyms"},
        format="json",
    )
    assert response.status_code == 201, response.data
    plan_id = response.data["id"]

    price = client.post(
        f"/api/platform/billing/plans/{plan_id}/prices/",
        {"interval": "monthly", "currency": "NGN", "amount": "25000.00"},
        format="json",
    )
    assert price.status_code == 201, price.data
    assert price.data["duration_months"] == 1

    gym = Gym.objects.create(name="Beta", plan_tier="medium")
    subscription = client.post(
        "/api/platform/billing/subscriptions/",
        {"gym_id": gym.pk, "plan_id": plan_id, "interval": "monthly"},
        format="json",
    )
    assert subscription.status_code == 201, subscription.data
    assert subscription.data["gym"]["id"] == gym.pk
    assert Invoice.objects.filter(gym=gym).count() == 1

    # A gym can only have one subscription.
    duplicate = client.post(
        "/api/platform/billing/subscriptions/",
        {"gym_id": gym.pk, "plan_id": plan_id, "interval": "monthly"},
        format="json",
    )
    assert duplicate.status_code == 400


def test_invoice_numbering_is_sequential_and_scoped_by_year():
    _, _, first = subscribed_gym("One", "+2347010000001")
    _, _, second = subscribed_gym("Two", "+2347010000002")
    year = timezone.localdate().year
    numbers = sorted(Invoice.objects.values_list("number", flat=True))
    assert numbers == [f"INV-{year}-00001", f"INV-{year}-00002"]
    assert first.invoices.count() == 1 and second.invoices.count() == 1


def test_payment_recording_and_partial_payments():
    gym, owner, subscription = subscribed_gym("One", "+2347010000001", amount="10000.00")
    invoice = subscription.invoices.get()

    payment = services.record_payment(invoice=invoice, amount=Decimal("4000.00"), recorded_by=owner)
    invoice.refresh_from_db()
    assert payment.amount == Decimal("4000.00")
    assert invoice.status == Invoice.Status.OPEN
    assert invoice.balance == Decimal("6000.00")

    services.record_payment(invoice=invoice, amount=Decimal("6000.00"), recorded_by=owner)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.balance == Decimal("0.00")


def test_void_requires_zero_payments_and_blocks_further_payment():
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()

    with pytest.raises(Exception):
        invoice.void(by=owner, reason="")
    assert invoice.status == Invoice.Status.OPEN

    invoice.void(by=owner, reason="Duplicate charge")
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.VOID

    with pytest.raises(Exception):
        services.record_payment(invoice=invoice, amount=Decimal("1000.00"), recorded_by=owner)


def test_payment_void_is_recorded_not_deleted():
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()
    payment = services.record_payment(invoice=invoice, amount=Decimal("1000.00"), recorded_by=owner)

    payment.void(by=owner, reason="Wrong amount")
    payment.refresh_from_db()
    assert payment.is_voided
    assert BillingPayment.objects.count() == 1


def test_generate_due_invoices_is_idempotent_and_advances_period():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym(
        "One", "+2347010000001", start=today - timedelta(days=40)
    )
    assert Invoice.objects.filter(subscription=subscription).count() == 1

    created = services.generate_due_invoices(today)
    assert created == 1
    subscription.refresh_from_db()
    assert subscription.current_period_end >= today
    assert Invoice.objects.filter(subscription=subscription).count() == 2

    # Rerunning the same day must not double-bill.
    assert services.generate_due_invoices(today) == 0
    assert Invoice.objects.filter(subscription=subscription).count() == 2


def test_mark_overdue_and_dunning_attempt():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()
    invoice.due_at = today - timedelta(days=7)
    invoice.save(update_fields=["due_at"])

    with override_settings(BILLING_DUNNING_ENABLED=True, BILLING_DUNNING_OFFSETS=[7]):
        sent = run_billing_dunning()

    assert sent == 1
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.OVERDUE
    attempt = DunningAttempt.objects.get(invoice=invoice)
    assert attempt.offset_days == 7
    assert attempt.status == DunningAttempt.Status.SENT


def test_dunning_is_idempotent_per_offset():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()
    invoice.due_at = today - timedelta(days=7)
    invoice.save(update_fields=["due_at"])

    with override_settings(BILLING_DUNNING_ENABLED=True, BILLING_DUNNING_OFFSETS=[7]):
        run_billing_dunning()
        run_billing_dunning()

    assert DunningAttempt.objects.filter(invoice=invoice).count() == 1


def test_auto_suspend_after_grace_period():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()
    invoice.due_at = today - timedelta(days=20)
    invoice.save(update_fields=["due_at"])

    with override_settings(
        BILLING_DUNNING_ENABLED=False,
        BILLING_AUTO_SUSPEND_ENABLED=True,
        BILLING_GRACE_PERIOD_DAYS=14,
    ):
        run_billing_dunning()

    gym.refresh_from_db()
    assert gym.is_active is False
    assert "unpaid invoice" in gym.suspension_reason


def test_auto_suspend_disabled_by_default_leaves_gym_active():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()
    invoice.due_at = today - timedelta(days=40)
    invoice.save(update_fields=["due_at"])

    run_billing_dunning()
    gym.refresh_from_db()
    assert gym.is_active is True


def _signed_webhook(secret, payload):
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()
    return body, signature


def test_paystack_webhook_records_payment_and_is_idempotent():
    gym, owner, subscription = subscribed_gym("One", "+2347010000001", amount="10000.00")
    invoice = subscription.invoices.get()
    invoice.reference = "ref-123"
    invoice.save(update_fields=["reference"])

    secret = "sk_test_secret"
    payload = {
        "event": "charge.success",
        "data": {
            "id": 555000111,
            "reference": "ref-123",
            "amount": 1000000,
            "metadata": {"invoice_id": invoice.pk},
        },
    }
    body, signature = _signed_webhook(secret, payload)
    client = APIClient()

    with override_settings(BILLING_PROVIDER="paystack", PAYSTACK_SECRET_KEY=secret):
        response = client.post(
            "/api/platform/billing/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
    assert response.status_code == 200, response.data

    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert BillingPayment.objects.filter(provider=BillingPayment.Provider.PAYSTACK).count() == 1

    with override_settings(BILLING_PROVIDER="paystack", PAYSTACK_SECRET_KEY=secret):
        duplicate = client.post(
            "/api/platform/billing/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
    assert duplicate.status_code == 200
    assert BillingPayment.objects.filter(provider=BillingPayment.Provider.PAYSTACK).count() == 1
    assert WebhookEvent.objects.count() == 1


def test_paystack_webhook_rejects_bad_signature():
    client = APIClient()
    with override_settings(BILLING_PROVIDER="paystack", PAYSTACK_SECRET_KEY="sk_test_secret"):
        response = client.post(
            "/api/platform/billing/webhooks/paystack/",
            data=b"{}",
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE="deadbeef",
        )
    assert response.status_code == 401


def test_owner_checkout_disabled_when_provider_manual():
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    invoice = subscription.invoices.get()
    response = client_for(owner).post(f"/api/billing/invoices/{invoice.pk}/checkout/")
    assert response.status_code == 400
    assert "not enabled" in response.data["detail"]


def test_owner_cannot_access_another_gyms_invoice():
    gym_a, owner_a, sub_a = subscribed_gym("A", "+2347010000001")
    gym_b, owner_b, sub_b = subscribed_gym("B", "+2347010000002")
    invoice_b = sub_b.invoices.get()

    response = client_for(owner_a).get(f"/api/billing/invoices/{invoice_b.pk}/")
    assert response.status_code == 404

    listing = client_for(owner_a).get("/api/billing/invoices/")
    assert listing.status_code == 200
    returned_ids = {row["id"] for row in listing.data["results"]}
    assert returned_ids == {sub_a.invoices.get().pk}


def test_platform_summary_reports_revenue_and_balance(super_admin):
    gym, owner, subscription = subscribed_gym("One", "+2347010000001", amount="12000.00")
    invoice = subscription.invoices.get()
    services.record_payment(invoice=invoice, amount=Decimal("2000.00"), recorded_by=owner)

    response = client_for(super_admin).get("/api/platform/billing/summary/")
    assert response.status_code == 200
    assert response.data["active_subscriptions"] == 1
    assert response.data["subscriptions_by_tier"] == {"small": 1}
    assert Decimal(response.data["monthly_recurring_revenue"]) == Decimal("12000.00")
    assert Decimal(response.data["outstanding_balance"]) == Decimal("10000.00")
    assert Decimal(response.data["collected_this_month"]) == Decimal("2000.00")


def test_platform_mark_paid_and_void_payment(super_admin):
    gym, owner, subscription = subscribed_gym("One", "+2347010000001", amount="10000.00")
    invoice = subscription.invoices.get()
    client = client_for(super_admin)

    paid = client.post(
        f"/api/platform/billing/invoices/{invoice.pk}/mark-paid/",
        {"method": "cash"},
        format="json",
    )
    assert paid.status_code == 201, paid.data
    payment_id = paid.data["id"]
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID

    voided = client.post(
        f"/api/platform/billing/payments/{payment_id}/void/",
        {"reason": "Recorded in error"},
        format="json",
    )
    assert voided.status_code == 200
    assert voided.data["is_voided"] is True


def test_platform_subscription_change_plan_applies_to_next_period():
    gym, owner, subscription = subscribed_gym("One", "+2347010000001")
    other = make_plan(tier="big", name="Big Plan")
    add_price(other, interval="annual", amount="120000.00")

    response = client_for(make_user("+2347000000001", User.Role.SUPER_ADMIN, name="Platform")).post(
        f"/api/platform/billing/subscriptions/{subscription.pk}/change-plan/",
        {"plan_id": other.pk, "interval": "annual"},
        format="json",
    )
    assert response.status_code == 200, response.data
    subscription.refresh_from_db()
    assert subscription.plan_id == other.pk
    assert subscription.interval == "annual"
    # The already-issued invoice for the current period is unchanged.
    assert subscription.invoices.get().amount == Decimal("10000.00")


def test_celery_task_generates_due_invoices_via_task_entrypoint():
    today = timezone.localdate()
    subscribed_gym("One", "+2347010000001", start=today - timedelta(days=40))
    assert generate_billing_invoices() == 1


@pytest.mark.parametrize(
    "interval,months",
    [("monthly", 1), ("quarterly", 3), ("semiannual", 6), ("annual", 12)],
)
def test_interval_periods_advance_on_full_payment(interval, months):
    from billing.models import add_months

    start = timezone.localdate()
    gym, owner, subscription = subscribed_gym(
        f"Gym {interval}", f"+23470200000{months:02d}", interval=interval, start=start
    )
    invoice = subscription.invoices.get()
    assert invoice.period_end == add_months(start, months) - timedelta(days=1)
    assert invoice.interval == interval

    services.record_payment(invoice=invoice, amount=invoice.amount, recorded_by=owner)

    subscription.refresh_from_db()
    assert subscription.current_period_start == add_months(start, months)
    assert subscription.current_period_end == add_months(start, months * 2) - timedelta(days=1)
    next_invoice = subscription.invoices.order_by("period_start").last()
    assert next_invoice.period_start == add_months(start, months)
    assert next_invoice.amount == invoice.amount


def test_overpayment_is_allowed_and_flagged():
    gym, owner, subscription = subscribed_gym("One", "+2347030000001", amount="10000.00")
    invoice = subscription.invoices.get()

    services.record_payment(invoice=invoice, amount=Decimal("12000.00"), recorded_by=owner)

    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.balance == Decimal("0.00")
    assert invoice.amount_paid == Decimal("12000.00")
    assert invoice.is_overpaid is True


def test_invoice_and_payment_snapshot_plan_and_amount():
    gym, owner, subscription = subscribed_gym("One", "+2347030000002", amount="10000.00")
    invoice = subscription.invoices.get()
    payment = services.record_payment(
        invoice=invoice, amount=Decimal("10000.00"), recorded_by=owner
    )

    subscription.plan.name = "Renamed Plan"
    subscription.plan.save(update_fields=["name"])
    add_price(
        subscription.plan,
        amount="25000.00",
        effective_from=timezone.localdate() + timedelta(days=1),
    )

    invoice.refresh_from_db()
    payment.refresh_from_db()
    assert invoice.plan_name == "One Plan"
    assert invoice.amount == Decimal("10000.00")
    assert payment.plan_id == subscription.plan_id
    assert payment.amount == Decimal("10000.00")


def test_voiding_a_payment_reverses_the_invoice():
    gym, owner, subscription = subscribed_gym("One", "+2347030000003", amount="10000.00")
    invoice = subscription.invoices.get()
    payment = services.record_payment(
        invoice=invoice, amount=Decimal("10000.00"), recorded_by=owner
    )

    payment.void(by=owner, reason="Bank reversal")

    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.OPEN
    assert invoice.amount_paid == Decimal("0.00")
    assert invoice.balance == Decimal("10000.00")


def test_checkout_amount_comes_from_the_invoice(monkeypatch):
    gym, owner, subscription = subscribed_gym("One", "+2347030000004", amount="10000.00")
    invoice = subscription.invoices.get()
    services.record_payment(invoice=invoice, amount=Decimal("3000.00"), recorded_by=owner)

    captured = {}

    def fake_initialize(*, email, amount, reference, callback_url="", metadata=None):
        captured.update(amount=amount, reference=reference, metadata=metadata)
        return {"authorization_url": "https://checkout.paystack.test/abc", "reference": reference}

    monkeypatch.setattr("billing.views.initialize_transaction", fake_initialize)
    with override_settings(BILLING_PROVIDER="paystack", PAYSTACK_SECRET_KEY="sk_test"):
        response = client_for(owner).post(f"/api/billing/invoices/{invoice.pk}/checkout/")

    assert response.status_code == 200, response.data
    assert captured["amount"] == Decimal("7000.00")
    assert captured["metadata"] == {"invoice_id": invoice.pk}
    assert response.data["amount"] == "7000.00"


def test_dunning_fires_before_the_due_date():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym("One", "+2347030000005")
    invoice = subscription.invoices.get()
    invoice.due_at = today + timedelta(days=3)
    invoice.save(update_fields=["due_at"])

    with override_settings(BILLING_DUNNING_ENABLED=True, BILLING_DUNNING_OFFSETS=[-3]):
        assert run_billing_dunning() == 1

    attempt = DunningAttempt.objects.get(invoice=invoice)
    assert attempt.offset_days == -3
    assert attempt.status == DunningAttempt.Status.SENT


def test_billing_run_processes_batched_subscriptions():
    today = timezone.localdate()
    for index in range(3):
        subscribed_gym(
            f"Batch {index}", f"+23470400000{index:02d}", start=today - timedelta(days=40)
        )

    with override_settings(BILLING_RUN_BATCH_SIZE=2):
        assert generate_billing_invoices() == 3


def test_auto_suspended_gym_can_be_reactivated():
    today = timezone.localdate()
    gym, owner, subscription = subscribed_gym("One", "+2347030000006")
    invoice = subscription.invoices.get()
    invoice.due_at = today - timedelta(days=20)
    invoice.save(update_fields=["due_at"])

    with override_settings(
        BILLING_DUNNING_ENABLED=False,
        BILLING_AUTO_SUSPEND_ENABLED=True,
        BILLING_GRACE_PERIOD_DAYS=14,
    ):
        run_billing_dunning()

    gym.refresh_from_db()
    assert gym.is_active is False

    gym.reactivate()
    gym.refresh_from_db()
    assert gym.is_active is True
    assert gym.suspension_reason == ""
