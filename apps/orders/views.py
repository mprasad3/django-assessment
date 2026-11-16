"""
Section 1 - Views demonstrating the N+1 problem and its fix.

ENDPOINT: GET /api/orders/summary/
BROKEN:   /api/orders/summary/?version=broken
FIXED:    /api/orders/summary/  (default)

Both views are decorated with @silk_profile so you can compare query counts
in the Silk dashboard at http://127.0.0.1:8000/silk/
"""
from django.contrib.auth.models import User
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from silk.profiling.profiler import silk_profile

from .models import Order
from .serializers import BrokenOrderSerializer, OrderSerializer


@api_view(["GET"])
@silk_profile(name="orders_summary_BROKEN")
def order_summary_broken(request: Request) -> Response:
    """
    BROKEN view - demonstrates the N+1 query problem.

    The queryset fetches all orders but does NOT pre-load related items or
    products. When BrokenOrderSerializer iterates over order.items.all() for
    each order, Django fires a new SELECT query to the database per order.
    Then for each item, BrokenOrderItemSerializer accesses item.product, which
    fires yet another SELECT per item.

    Root cause: The serializer implicitly traverses two levels of foreign-key
    relationships (order->items and item->product) that have not been eagerly
    loaded. Each traversal hits the database.

    For a user with 200 orders and ~5 items each:
      1 query  (fetch orders)
      + 200 queries (items per order)
      + 1000 queries (product per item)
      + 200 queries (item count per order via .count())
    = ~1,401 database queries for a single API request.
    """
    # Simulate: fetch orders for the authenticated user (or user pk=1 in demo)
    user = _get_demo_user(request)
    orders = Order.objects.filter(user=user).order_by("-created_at")
    serializer = BrokenOrderSerializer(orders, many=True)
    return Response({"count": orders.count(), "results": serializer.data})


@api_view(["GET"])
@silk_profile(name="orders_summary_FIXED")
def order_summary_fixed(request: Request) -> Response:
    """
    FIXED view - eliminates the N+1 problem with prefetch_related.

    FIX EXPLANATION:
    prefetch_related('items__product') tells Django's ORM to execute two
    additional SQL queries upfront using IN clauses:

      Query 2: SELECT * FROM order_item
               WHERE order_id IN (1, 2, 3, ... 200)

      Query 3: SELECT * FROM product
               WHERE id IN (10, 11, ... N_distinct_products)

    Django then hydrates the ORM objects and stores the related objects in an
    internal per-queryset cache (_prefetched_objects_cache). When the serializer
    accesses order.items.all() or item.product, Django reads from this cache
    rather than issuing SQL. The result is exactly 3 queries regardless of how
    many orders or items exist.

    select_related('user') is also added: it JOINs the auth_user table into
    the initial ORDER BY query, eliminating a separate SELECT per order if the
    user field is accessed.

    At the database level: prefetch_related uses an SQL IN clause which lets
    the database use the existing primary-key index to satisfy the query in a
    single B-tree scan, rather than N separate index lookups.
    """
    user = _get_demo_user(request)
    orders = (
        Order.objects.filter(user=user)
        .select_related("user")
        .prefetch_related("items__product")
        .order_by("-created_at")
    )
    serializer = OrderSerializer(orders, many=True)
    return Response({"count": orders.count(), "results": serializer.data})


def _get_demo_user(request: Request) -> User:
    """Return authenticated user or fall back to the first seeded user."""
    if request.user.is_authenticated:
        return request.user
    return User.objects.first()
