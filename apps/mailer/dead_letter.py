"""
Section 2 - Dead-Letter Queue.

When a Celery task exceeds max_retries, it moves the job payload to a Redis
list (`dead_letter:emails`). This is a simple, inspectable list that ops can
query and requeue without touching the Celery broker.

DESIGN DECISION:
  We store the failed job in a Redis LIST rather than a separate Celery queue
  because:
  1. It is immediately inspectable with `redis-cli LRANGE dead_letter:emails 0 -1`
  2. Requeue is a manual operation (intentional) - permanently failed jobs should
     not auto-retry without human intervention.
  3. It avoids complicating the Celery task graph with a "poison pill" queue that
     could loop back into the main queue.
"""
import json
import time
import redis
from django.conf import settings


DEAD_LETTER_KEY = "dead_letter:emails"


class DeadLetterQueue:
    """
    Stores permanently failed email jobs in a Redis list.
    """

    def __init__(self, redis_client: redis.Redis = None):
        self._redis = redis_client

    @property
    def redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis.from_url(
                getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
        return self._redis

    def push(self, job_payload: dict, reason: str = "max_retries_exceeded") -> None:
        """
        Push a permanently failed job to the dead-letter list.

        Args:
            job_payload: The original job data dict.
            reason: Human-readable failure reason for diagnostics.
        """
        entry = {
            "payload": job_payload,
            "reason": reason,
            "failed_at": time.time(),
            "failed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            self.redis.rpush(DEAD_LETTER_KEY, json.dumps(entry))
        except redis.exceptions.ConnectionError:
            # Log the loss - in production, write to a local fallback file
            import logging
            logging.getLogger(__name__).error(
                "Failed to push to dead-letter queue (Redis unavailable). "
                "Job lost: %s",
                job_payload,
            )

    def length(self) -> int:
        """Return the number of jobs in the dead-letter queue."""
        try:
            return self.redis.llen(DEAD_LETTER_KEY)
        except redis.exceptions.ConnectionError:
            return 0

    def peek(self, count: int = 10) -> list[dict]:
        """Return the first `count` entries without removing them."""
        try:
            raw = self.redis.lrange(DEAD_LETTER_KEY, 0, count - 1)
            return [json.loads(r) for r in raw]
        except redis.exceptions.ConnectionError:
            return []

    def pop(self) -> dict | None:
        """Pop and return the oldest dead-letter entry."""
        try:
            raw = self.redis.lpop(DEAD_LETTER_KEY)
            return json.loads(raw) if raw else None
        except redis.exceptions.ConnectionError:
            return None

    def flush(self) -> int:
        """Remove all entries (for testing)."""
        try:
            count = self.redis.llen(DEAD_LETTER_KEY)
            self.redis.delete(DEAD_LETTER_KEY)
            return count
        except redis.exceptions.ConnectionError:
            return 0


# Module-level default instance
default_dlq = DeadLetterQueue()
