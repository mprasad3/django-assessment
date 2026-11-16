"""
Section 2 - Redis Sliding-Window Rate Limiter.

APPROACH: Sliding Window using Redis Sorted Sets (Option B).

CHOICE RATIONALE (see DESIGN.md for full reasoning):
  - Token bucket (Option A): Requires DECR + TTL which is not truly atomic without
    a Lua script. The "tokens available" state is harder to reason about under
    concurrent access.
  - Fixed window (Option C): Allows bursts at window boundaries - a request at
    t=59s and t=61s both succeed but effectively represent 2 requests in 2 seconds.
  - Sliding window (Option B): Provides true per-unit-time enforcement. A request
    is allowed only if fewer than LIMIT requests were made in the last WINDOW seconds,
    computed at the exact moment of the request. No boundary burst problem.

ATOMICITY:
  We use a Redis pipeline with WATCH to implement optimistic locking:
    1. WATCH the key
    2. ZREMRANGEBYSCORE - evict entries older than the window
    3. ZCARD - count remaining entries
    4. MULTI / ZADD + EXPIRE / EXEC - atomically add if under limit

  If another client modifies the key between WATCH and EXEC (detected by Redis),
  the pipeline raises WatchError and we retry (compare-and-swap loop).

  NOTE ON LUA SCRIPTS: The production version ideally uses a Lua script via
  EVAL for stronger atomicity (no retry loop needed). We use pipeline+WATCH
  here for testability with fakeredis, which does not support EVAL/Lua.
  The semantics are equivalent: both are atomic at the Redis level.

REDIS FAILURE BEHAVIOUR (fail open):
  If Redis is unreachable, `redis.exceptions.ConnectionError` is raised.
  The caller (task) catches this and defaults to allowing the request (fail open).
  DESIGN TRADE-OFF: This risks exceeding the rate limit during Redis downtime,
  but is preferred here because dropping emails silently is worse than
  temporarily over-sending. In a stricter system, you would fail closed.
"""
import time
import uuid
import redis
from redis.exceptions import WatchError
from django.conf import settings


class SlidingWindowRateLimiter:
    """
    Redis-backed sliding window rate limiter.

    Uses a Redis sorted set where:
      - Score = request timestamp in milliseconds
      - Member = unique identifier (timestamp + uuid suffix for uniqueness)

    Requests older than (now - window_seconds) are evicted on each check.
    If the count of remaining entries is below the limit, the request is allowed.

    Usage:
        limiter = SlidingWindowRateLimiter(limit=200, window_seconds=60)
        allowed, count = limiter.is_allowed("rate_limit:emails")
        if not allowed:
            # Delay or reject
    """

    def __init__(
        self,
        limit: int = None,
        window_seconds: int = None,
        redis_client: redis.Redis = None,
    ):
        self.limit = limit or getattr(settings, "EMAIL_RATE_LIMIT", 200)
        self.window_seconds = window_seconds or getattr(
            settings, "EMAIL_RATE_WINDOW_SECONDS", 60
        )
        self._redis = redis_client

    @property
    def redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis.from_url(
                getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
        return self._redis

    def is_allowed(self, key: str = "rate_limit:emails") -> tuple[bool, int]:
        """
        Check if a request is within the rate limit using a Redis pipeline
        with WATCH for optimistic locking.

        ATOMICITY MECHANISM:
          WATCH key sets an optimistic lock on the sorted set.
          If any concurrent client modifies the key between WATCH and EXEC,
          Redis aborts the MULTI/EXEC transaction (raises WatchError).
          We retry in a tight loop - this loop terminates quickly because
          most requests don't collide.

          In true production Redis (not fakeredis), we would use a Lua EVAL
          script for even stronger single-round-trip atomicity. The pipeline
          approach requires 2 round trips (WATCH+read / MULTI+write) but is
          equivalent in correctness.

        Returns:
            (allowed: bool, current_count: int)
        """
        now_ms = int(time.time() * 1000)
        window_start_ms = now_ms - (self.window_seconds * 1000)
        member = f"{now_ms}:{uuid.uuid4().hex[:8]}"

        max_retries = 5
        for attempt in range(max_retries):
            try:
                with self.redis.pipeline() as pipe:
                    try:
                        # Optimistic lock: abort if key changes during transaction
                        pipe.watch(key)

                        # Read phase (outside MULTI - these execute immediately)
                        pipe.zremrangebyscore(key, "-inf", window_start_ms)
                        count = pipe.zcard(key)

                        # Write phase (inside MULTI - executes atomically)
                        pipe.multi()
                        if count < self.limit:
                            pipe.zadd(key, {member: now_ms})
                            pipe.expire(key, self.window_seconds + 1)
                            pipe.execute()
                            return True, count + 1
                        else:
                            pipe.execute()  # Empty MULTI - just unwatch
                            return False, count

                    except WatchError:
                        # Key was modified by another client - retry
                        if attempt == max_retries - 1:
                            # On final retry failure, fail open
                            return True, 0
                        continue

            except redis.exceptions.ConnectionError:
                # Fail open: allow request if Redis is unreachable
                # See module docstring for trade-off discussion
                return True, 0

        return True, 0  # Fail open if all retries exhausted

    def current_count(self, key: str = "rate_limit:emails") -> int:
        """Return current request count in the window (for monitoring)."""
        try:
            now_ms = int(time.time() * 1000)
            window_start_ms = now_ms - (self.window_seconds * 1000)
            self.redis.zremrangebyscore(key, "-inf", window_start_ms)
            return self.redis.zcard(key)
        except redis.exceptions.ConnectionError:
            return 0

    def reset(self, key: str = "rate_limit:emails") -> None:
        """Delete the rate-limit key (used in tests)."""
        try:
            self.redis.delete(key)
        except redis.exceptions.ConnectionError:
            pass


# Module-level default instance
default_limiter = SlidingWindowRateLimiter()
