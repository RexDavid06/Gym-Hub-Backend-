from django.urls import path

from billing.views import (
    OwnerBillingInvoiceCheckoutView,
    OwnerBillingInvoiceDetailView,
    OwnerBillingInvoiceListView,
    OwnerBillingPaymentListView,
    OwnerBillingSubscriptionView,
)

urlpatterns = [
    path(
        "subscription/", OwnerBillingSubscriptionView.as_view(), name="owner_billing_subscription"
    ),
    path("invoices/", OwnerBillingInvoiceListView.as_view(), name="owner_billing_invoice_list"),
    path(
        "invoices/<int:pk>/",
        OwnerBillingInvoiceDetailView.as_view(),
        name="owner_billing_invoice_detail",
    ),
    path(
        "invoices/<int:pk>/checkout/",
        OwnerBillingInvoiceCheckoutView.as_view(),
        name="owner_billing_invoice_checkout",
    ),
    path("payments/", OwnerBillingPaymentListView.as_view(), name="owner_billing_payment_list"),
]
