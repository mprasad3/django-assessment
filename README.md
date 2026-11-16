# Django Technical Assessment

A full-stack Django project demonstrating N+1 query diagnosis, rate-limited async job queues, and systems architecture reasoning.

---

## Quick Start (under 5 minutes)

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (for Redis)
- Git

### 1. Clone & set up the environment

```bash
git clone https://github.com/mprasad3/django-assessment.git
cd django-assessment

python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Start Redis

```bash
docker-compose up -d
```

Verify Redis is running:
```bash
docker-compose ps
```

### 3. Run database migrations & seed data

```bash
python manage.py migrate
python manage.py seed_data
```

This creates a demo user with 250 orders and ~1,250 order items, alongside 8 sample products.

### 4. Run the development server

```bash
python manage.py runserver
```

---

## Running the Tests

```bash
pytest -v
```

All 17 tests verify rate limiting, retry backoff, dead-letter queue routing, and query count optimizations.

---

## Section 1 - Broken System Demo

With the development server running and data seeded:

| Endpoint | Description |
|----------|-------------|
| GET /api/orders/summary/ | Fixed view (3 queries, ~15ms) |
| GET /api/orders/summary/broken/ | Broken view (1,250+ queries, ~10s) |
| http://127.0.0.1:8000/silk/ | Silk profiler dashboard |

### Viewing Silk profiler evidence

1. Visit http://127.0.0.1:8000/api/orders/summary/broken/
2. Visit http://127.0.0.1:8000/api/orders/summary/
3. Open http://127.0.0.1:8000/silk/requests/ to inspect query counts and execution times side by side.

---

## Section 2 - Job Queue Demo

### Start Celery worker (requires Redis)

```bash
celery -A celery_app worker --loglevel=info
```

### Submit test jobs manually

```python
# In Django shell: python manage.py shell
from apps.mailer.tasks import send_email_task
from apps.mailer.rate_limiter import SlidingWindowRateLimiter
import redis

r = redis.Redis(decode_responses=True)
limiter = SlidingWindowRateLimiter(redis_client=r)

# Submit 100 jobs
for i in range(100):
    send_email_task.delay(
        recipient=f"user{i}@example.com",
        subject=f"Order #{i} confirmed",
        body="Your order has been placed."
    )

print("Current count in window:", limiter.current_count())
```

### Monitor Redis queue state

```bash
# Queue depth
redis-cli LLEN celery

# Dead-letter queue
redis-cli LRANGE dead_letter:emails 0 -1

# Rate limiter state
redis-cli ZRANGE rate_limit:emails 0 -1 WITHSCORES
redis-cli ZCARD rate_limit:emails
```

---

## Project Structure

```
django-assessment/
|-- README.md                    # Setup and execution guide
|-- DESIGN.md                    # Section 2 architecture decisions
|-- ANSWERS.md                   # Written answers for Sections 1, 2, 3
|-- requirements.txt
|-- docker-compose.yml           # Redis container
|-- pytest.ini
|-- conftest.py
|-- manage.py
|-- celery_app.py                # Celery configuration
|-- config/
|   |-- settings/
|   |   \-- base.py              # Django settings
|   \-- urls.py
\-- apps/
    |-- orders/                  # Section 1: N+1 query diagnosis
    |   |-- models.py            # Order, OrderItem, Product
    |   |-- views.py             # Broken and Fixed views
    |   |-- serializers.py       # Serializers
    |   |-- urls.py
    |   \-- tests.py             # Query count tests
    \-- mailer/                  # Section 2: Job queue
        |-- tasks.py             # Celery task and retry logic
        |-- rate_limiter.py      # Redis sliding-window rate limiter
        |-- dead_letter.py       # Dead-letter queue
        \-- tests.py             # 500-job guarantee tests
```

---

## Written Answers and Architecture Decisions

- Section 1: Incident investigation log and ORM root cause breakdown in ANSWERS.md
- Section 2: Architecture comparison (Celery vs Django Q vs Custom) and sliding window rate limiter design in DESIGN.md
- Section 2: SIGKILL crash handling explanation in ANSWERS.md
- Section 3: Question A (Django Admin performance) and Question B (Pagination trade-offs) in ANSWERS.md

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| REDIS_URL | redis://localhost:6379/0 | Redis connection URL |
| DJANGO_SETTINGS_MODULE | config.settings.base | Settings module |

No secrets or credentials are committed to this repository.
