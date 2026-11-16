"""
Section 1 - Seed management command.

Creates a demo user with 250 orders (5 items each) to reliably trigger
the N+1 timeout scenario and demonstrate the query count difference.

Usage:
    python manage.py seed_data
"""
import random
import decimal
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from apps.orders.models import Order, OrderItem, Product


PRODUCTS = [
    ("Widget Pro", "WGT-001", "29.99"),
    ("Gadget Ultra", "GDG-002", "49.99"),
    ("Doohickey X", "DHK-003", "14.99"),
    ("Thingamajig", "TMJ-004", "9.99"),
    ("Contraption Z", "CTZ-005", "79.99"),
    ("Gizmo Lite", "GZM-006", "19.99"),
    ("Apparatus A", "APR-007", "34.99"),
    ("Device B", "DVB-008", "54.99"),
]


class Command(BaseCommand):
    help = "Seed the database with demo orders to demonstrate the N+1 problem"

    def add_arguments(self, parser):
        parser.add_argument(
            "--orders",
            type=int,
            default=250,
            help="Number of orders to create (default: 250)",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Delete existing data before seeding",
        )

    def handle(self, *args, **options):
        if options["clean"]:
            self.stdout.write("Cleaning existing data...")
            OrderItem.objects.all().delete()
            Order.objects.all().delete()
            Product.objects.all().delete()
            User.objects.filter(username="demo_user").delete()

        # Create products
        products = []
        for name, sku, price in PRODUCTS:
            p, _ = Product.objects.get_or_create(
                sku=sku,
                defaults={"name": name, "unit_price": decimal.Decimal(price)},
            )
            products.append(p)
        self.stdout.write(f"  [OK] {len(products)} products ready")

        # Create demo user
        user, _ = User.objects.get_or_create(
            username="demo_user",
            defaults={"email": "demo@example.com"},
        )
        user.set_password("demo_password")
        user.save()
        self.stdout.write("  [OK] User 'demo_user' ready (password: demo_password)")

        # Create orders
        num_orders = options["orders"]
        statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
        orders_created = 0
        items_created = 0

        for i in range(num_orders):
            order = Order.objects.create(
                user=user,
                status=random.choice(statuses),
            )
            # 3-7 items per order
            num_items = random.randint(3, 7)
            selected_products = random.sample(products, min(num_items, len(products)))
            for product in selected_products:
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=random.randint(1, 5),
                    unit_price=product.unit_price,
                )
                items_created += 1
            orders_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeeded {orders_created} orders with {items_created} items "
                f"for user 'demo_user'\n"
                f"   Run: python manage.py runserver\n"
                f"   Then visit:\n"
                f"     Broken: http://127.0.0.1:8000/api/orders/summary/broken/\n"
                f"     Fixed:  http://127.0.0.1:8000/api/orders/summary/\n"
                f"     Silk:   http://127.0.0.1:8000/silk/"
            )
        )
