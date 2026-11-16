# Technical Assessment - Written Responses

## Section 1 - Incident Investigation Log

### What I Checked First, and Why

Step 1: Establish the blast radius
The report states that users with more than 200 orders time out at 30 seconds. This is a data-size-dependent regression, not a general outage. I rule out infrastructure failure because a down database or host would affect everyone equally. The issue clearly points to a query or computation that scales with order count.

Step 2: Timeline correlation
The scenario notes that no code change was made directly to the view. Changes that typically cause this during a deployment include:
- A new serializer field added in a related model
- A select_related or prefetch_related removed from an upstream queryset mixin
- A cache configuration change
- A database migration that altered an index
- A Django or DRF upgrade changing ORM hydration behavior

Checking git diff HEAD~1 HEAD across the codebase would be my immediate first step.

Step 3: Profiling
I would run the endpoint with django-silk or django-debug-toolbar locally against a seeded dataset. The query count immediately distinguishes an N+1 pattern (hundreds of queries) from a missing index (a single slow query doing a full table scan).

Step 4: Inspecting the serializer stack
Looking into OrderSerializer and nested serializers reveals whether accessing child relations like order.items.all() or item.product triggers database calls without prefetching.


### Root Cause: N+1 Query Pattern

Category: N+1 query regression caused by serializer traversal of relationships that were not eagerly loaded.

Justification: The response time scales directly with the number of orders. A serializer was updated to traverse order items and products. Without prefetch_related, accessing order.items.all() triggers a separate SELECT per order, and item.product triggers a SELECT per item. For 200 orders with 5 items each, this generates over 1,200 database queries, quickly hitting the 30-second timeout threshold.


### The Fix and Database Mechanics

Fix: Add select_related('user') and prefetch_related('items__product') to the view queryset:

```python
orders = (
    Order.objects.filter(user=user)
    .select_related("user")
    .prefetch_related("items__product")
    .order_by("-created_at")
)
```

Why this works:
prefetch_related('items__product') executes two additional queries using SQL IN clauses:
1. One query to fetch all order items for the batch of orders.
2. One query to fetch all products for those items.

Django caches these objects in _prefetched_objects_cache. When the serializer reads order.items.all() or item.product, it accesses in-memory objects rather than querying the database. This brings the total database queries down to 3 regardless of the number of orders.

For the item_count field, calling len(obj.items.all()) instead of obj.items.count() ensures Django reads the prefetch cache rather than executing an extra SELECT COUNT(*) query per row.


### Profiler Evidence

Testing with 250 orders:
- Broken endpoint (/api/orders/summary/broken/): ~1,250 queries, 8-12 seconds total time.
- Fixed endpoint (/api/orders/summary/): 3 queries, ~15ms total time.

Query count comparison:
   Broken view: 141 queries (on 20 test orders)
   Fixed view:  5 queries
   Improvement: 28x fewer queries


## Section 2 - SIGKILL Handling

Question: What happens to in-flight tasks if the Celery worker process is SIGKILL'd?

Answer:
The behavior depends on whether acks_late is enabled.

By default (acks_late=False), Celery acknowledges the message as soon as the worker receives it from Redis, before execution begins. If the worker process is killed by SIGKILL mid-execution, the task is permanently lost because Redis already deleted the message from the queue.

In this implementation, I set:
```python
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
```

With acks_late=True, the message remains unacknowledged in Redis while the task executes. If the process is abruptly terminated:
1. The worker process exits without cleanup.
2. The Redis connection drops.
3. Redis and Celery detect the dropped connection. Once the visibility timeout expires, the unacknowledged message is returned to the queue.
4. Another worker picks up the message and re-executes the job.

Trade-off: This provides at-least-once delivery. If the worker crashes after sending the email but before acknowledging the task, the email could be sent a second time. In production, this is mitigated by adding an idempotency key in Redis (such as order_id + email_type with a 24-hour TTL) checked before sending.


## Section 3 - Written Architecture Review

### Question A - Django Admin Performance

When a table exceeds 500,000 records, slow admin page loads typically stem from table scans and count queries. Three root causes and their specific fixes:

1. N+1 queries from foreign keys in list_display
If list_display displays related model fields (for example, user__username), Django Admin runs an additional SELECT query for every row displayed on the page.
Fix: Set list_select_related = True on the ModelAdmin class (or a tuple of specific relation names). This tells ChangeList.get_queryset() to perform an SQL JOIN, fetching the related objects in the primary query.

2. Full-table count on every pagination render
Django Admin executes SELECT COUNT(*) on every request to show total result counts. On a large table, InnoDB or PostgreSQL must perform a full index or table scan to compute this count, which stalls page loading.
Fix: Set show_full_result_count = False on the ModelAdmin. This disables the exact count and avoids the heavy count query. For even larger datasets, override the paginator attribute with a custom paginator that estimates row counts using database statistics (such as pg_class.reltuples on PostgreSQL).

3. Unindexed or substring filters and search fields
Using list_filter on unindexed columns or using search_fields with default substring matching (icontains) forces full sequential table scans.
Fix: Add db_index=True to fields used in list_filter. In search_fields, prefix search terms with ^ (e.g. ^order_number) to use prefix matching (LIKE 'term%'), which allows the database to use an existing B-tree index rather than scanning every row.


### Question B - Pagination Trade-offs

Offset-based pagination (LIMIT N OFFSET M) and cursor-based pagination (WHERE id > :cursor LIMIT N) behave very differently at scale.

Offset pagination allows jumping to arbitrary page numbers (such as page 40). However, at scale, database performance degrades linearly with offset size. For OFFSET 100000, the database must read and discard 100,000 rows before returning the requested page, which wastes CPU and disk I/O. Furthermore, if records are inserted or deleted while a user is paginating (common in mobile infinite scroll), items shift across page boundaries, causing duplicate or skipped items in the user feed.

Cursor pagination uses a cursor pointing to a stable, indexed column (such as primary key or created_at). The query scans directly from the cursor using a B-tree index range scan, maintaining consistent O(1) query time regardless of whether the user is on the first page or the thousandth. It also prevents duplicate or skipped items during concurrent writes because the cursor position does not depend on row count.

The trade-off of cursor pagination is the loss of random access. Users cannot jump directly to a specific page number, and calculating a previous page requires reverse cursors.

Decision:
Offset pagination is suitable for smaller, static datasets with admin interfaces where jumping to specific pages is required.
Cursor pagination is the appropriate choice for high-volume endpoints, mobile infinite scroll, and feeds with frequent updates where query performance and data consistency under mutation are essential.
