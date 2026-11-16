# Architecture Design: Rate-Limited Async Job Queue

## Problem Statement

The system must send transactional emails (order confirmations, OTPs, alerts) under these conditions:
- Third-party provider rate limit: 200 emails per minute
- Burst traffic: 2,000 requests in under 10 seconds during flash sales
- Core guarantees: zero job loss on worker crash, automatic retries with exponential backoff


## Architecture Decision: Celery + Redis

### Options Evaluated

| Criterion | Celery + Redis | Django Q | Custom Queue |
|-----------|---------------|----------|--------------|
| Crash safety | Full (acks_late + reject_on_worker_lost) | Limited (depends on ORM broker) | Must implement manually |
| Retry + backoff | First-class API (self.retry, countdown) | Basic retry, limited backoff control | Fully custom |
| Rate limiting hook | Clean (task inspects and reschedules) | No native mechanism | Full control |
| Operational maturity | High (Flower, Prometheus, production proven) | Moderate | None (self-managed) |
| Setup complexity | Moderate (requires Redis + worker) | Low (uses Django database) | Low to high |
| Burst tolerance | High (Redis LIST absorbs bursts O(1)) | Moderate (DB writes buffer) | Depends on storage |

### Why Celery + Redis

Django Q is convenient because it does not require a separate broker process, but its retry support lacks fine-grained backoff control. More importantly, its message acknowledgement semantics depend on database transactions, which introduces risk under database connection drops.

A custom implementation would need to handle worker crash detection, visibility timeouts, and retry scheduling from scratch. Re-implementing these reliability primitives is error-prone.

Celery paired with Redis provides:
1. Fast buffer ingestion: Redis LIST operations (RPUSH and BLPOP) absorb 2,000 requests in milliseconds without locking database tables.
2. Crash recovery: Celery supports acks_late and reject_on_worker_lost natively, ensuring dropped workers do not lose jobs.
3. Clean non-blocking rate limiting: Tasks that exceed the rate limit reschedule themselves with a countdown ETA rather than freezing the worker thread.


## Rate Limiter Design

### Algorithm Choice: Sliding Window

Why not Token Bucket?
Token bucket requires tracking both current token counts and refill timestamps across workers. Doing so atomically requires extra coordination, and recovering bucket state after restarts adds complexity.

Why not Fixed Window?
Fixed windows suffer from boundary bursts. If 200 requests arrive at second 59 and another 200 arrive at second 61, 400 emails fire across a 2-second interval, violating the provider limit.

Why Sliding Window?
A sliding window backed by a Redis sorted set computes the exact count of requests within the preceding 60 seconds at any moment. Requests older than the current window are evicted using ZREMRANGEBYSCORE, and new timestamps are added using ZADD. This guarantees an accurate rolling rate limit without boundary spikes. Memory overhead is minimal: 200 entries occupy roughly 10 KB in Redis.

### Atomicity via Redis Pipeline and WATCH

The limiter uses optimistic locking with a Redis pipeline:

```python
with redis.pipeline() as pipe:
    pipe.watch(key)
    pipe.zremrangebyscore(key, "-inf", window_start_ms)
    count = pipe.zcard(key)
    pipe.multi()
    if count < limit:
        pipe.zadd(key, {member: now_ms})
        pipe.expire(key, window_seconds + 1)
    pipe.execute()
```

If another process modifies the set between WATCH and EXECUTE, Redis aborts the transaction and raises WatchError, prompting a retry. This provides safe concurrency without requiring external locks.

### Redis Failure Behavior

Decision: Fail open.
If Redis becomes unreachable, the rate limiter allows the email to send rather than blocking.

Trade-off: A Redis outage risks exceeding the provider limit during that downtime. However, for transactional messaging (such as password resets and purchase confirmations), sending with risk of provider throttling is preferable to dropping critical business emails entirely.


## Crash Safety and Worker Loss

What happens when a worker process is killed by SIGKILL?

Default behavior (unsafe):
By default, Celery acknowledges messages upon receipt before processing starts. If a worker receives SIGKILL mid-task, the message is already gone from Redis, resulting in permanent job loss.

Safe configuration used:
```python
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
```

Under this configuration:
1. The message stays unacknowledged in Redis during task execution.
2. If the worker process crashes, its connection to Redis drops.
3. Celery transport visibility timeout detects the abandoned message and re-queues it.
4. An active worker picks up the message and resumes execution.

Trade-off: This provides at-least-once delivery. If the process terminates after sending the email but before acknowledging the task, a duplicate email could be sent. In production, this can be addressed by setting a unique idempotency key in Redis prior to calling the email provider API.


## Dead-Letter Queue

Tasks that exceed their maximum retry count (3 attempts) are written to a Redis list at `dead_letter:emails`.

Storing failed payloads in a dedicated Redis list provides:
- Quick inspection with `redis-cli LRANGE dead_letter:emails 0 -1`
- Separation from active queues so poison-pill payloads do not trigger infinite retry loops
- A clean handoff for manual review or re-driving


## System Flow

```
Flash Sale Burst (2,000 req/10s)
        |
        v
  Django API view
   apply_async()         <-- O(1), non-blocking
        |
        v
  Redis RPUSH            <-- absorbs burst instantly
  (Celery broker list)
        |
        v
  Celery Worker(s)       <-- BLPOP, processes at sustainable rate
        |
        +--- Rate limiter check (Sliding Window)
        |       |-- Allowed -> send email
        |       \-- Exceeded -> retry(countdown=1) [no sleep]
        |
        +--- Send success -> ACK -> done
        |
        \--- Send failure -> retry(countdown=2^n)
                |
                \-- MaxRetries -> push to dead_letter:emails LIST
```
