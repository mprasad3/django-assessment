"""
Section 1 - URL configuration for orders app.
"""
from django.urls import path
from . import views

urlpatterns = [
    # Fixed endpoint (default) - 3 queries
    path("orders/summary/", views.order_summary_fixed, name="order-summary"),
    # Broken endpoint - for demonstration/comparison purposes
    path("orders/summary/broken/", views.order_summary_broken, name="order-summary-broken"),
]
