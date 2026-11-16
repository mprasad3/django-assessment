"""
Section 1 - Serializers demonstrating the N+1 problem and its fix.

BROKEN: OrderItemSerializer accesses `item.product.name` during serialisation.
When the parent serializer iterates over `order.items.all()` for each order,
Django fires:
  1 query  -> fetch user's orders (SELECT * FROM order WHERE user_id = ?)
  N queries -> fetch items per order (SELECT * FROM order_item WHERE order_id = ?)
  M queries -> fetch product per item (SELECT * FROM product WHERE id = ?)

For 200 orders x avg 5 items = 1 + 200 + 1000 = 1,201 queries.

FIXED: `prefetch_related('items__product')` in the view queryset instructs
Django to issue exactly 2 additional SQL queries using IN clauses:
  SELECT * FROM order_item WHERE order_id IN (1, 2, ... 200)
  SELECT * FROM product WHERE id IN (10, 11, ... N)

Django then maps the results in Python without hitting the database again.
Total: 3 queries regardless of order/item count.
"""
from rest_framework import serializers
from .models import Order, OrderItem, Product


#  BROKEN serializers (N+1 problem) 

class BrokenProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "sku"]


class BrokenOrderItemSerializer(serializers.ModelSerializer):
    # This field triggers a separate SELECT query for each item's product
    product = BrokenProductSerializer(read_only=True)
    line_total = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity", "unit_price", "line_total"]


class BrokenOrderSerializer(serializers.ModelSerializer):
    # `items` accessor calls order.items.all() - a fresh DB hit per order
    items = BrokenOrderItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "status", "created_at", "item_count", "items"]

    def get_item_count(self, obj):
        # Additional query per order: SELECT COUNT(*) FROM order_item WHERE order_id = ?
        return obj.items.count()


#  FIXED serializers 

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "sku"]


class OrderItemSerializer(serializers.ModelSerializer):
    # After prefetch_related('items__product'), accessing item.product here
    # reads from Django's internal prefetch cache - zero additional queries.
    product = ProductSerializer(read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity", "unit_price", "line_total"]

    def get_line_total(self, obj):
        return obj.quantity * obj.unit_price


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    # Use len() on the prefetched cache - does NOT issue a COUNT query
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "status", "created_at", "item_count", "items"]

    def get_item_count(self, obj):
        # len() on prefetched queryset reads from cache - no extra DB query
        return len(obj.items.all())
