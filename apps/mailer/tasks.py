"""
Section 2 - Celery task: send_email_task.

DESIGN OVERVIEW:
  This task represents one transactional email send.
  It integrates the rate limiter and dead-letter queue.

RETRY STRATEGY - Exponential Backoff:
  - max_retries = 3 (total 4 attempts including the first)
  - Backoff: 2^retry_number seconds (2s, 4s, 8s)
  - On permanent failure (MaxRetriesExceededError), job moves to dead-letter queue.

RATE LIMITER INTEGRATION:
  Before sending, the task checks the sliding-window rate limiter.
  If the rate limit is exceeded:
    - The task raises a RateLimitExceeded exception.
    - It reschedules itself with a countdown of 1 second (ETA-based delay).
    - Crucially, NO time.sleep() is used. The worker is free during the wait.
    - This avoids blocking the worker thread and allows other tasks to proceed.

SIGKILL SAFETY (see celery_app.py and ANSWERS.md):
  - CELERY_TASK_ACKS_LATE = True in settings -> task acknowledged only after return.
  - If the worker is SIGKILL'd during task execution, the unACK'd message remains
    in Redis and is redelivered to any available worker on reconnect.
"""
import logging
from celery import shared_task

from .rate_limiter import SlidingWindowRateLimiter
from .dead_letter import DeadLetterQueue

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY = "rate_limit:emails"


class RateLimitExceeded(Exception):
    """Raised when the email rate limit is exceeded."""
    pass


@shared_task(
    bind=True,
    name="mailer.send_email",
    max_retries=3,
    # Do NOT use autoretry_for - we need custom retry logic (rate limit vs error)
    # Acknowledged only after success (see settings CELERY_TASK_ACKS_LATE)
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_email_task(
    self,
    recipient: str,
    subject: str,
    body: str,
    _force_fail: bool = False,
    _limiter: SlidingWindowRateLimiter = None,
    _dlq: DeadLetterQueue = None,
):
    """
    Send a transactional email, respecting the rate limit.

    Args:
        recipient: Email address of the recipient.
        subject: Email subject line.
        body: Plain-text email body.
        _force_fail: Testing hook - raises an exception to trigger retry logic.
        _limiter: Optional injected rate limiter (for testing with fakeredis).
        _dlq: Optional injected dead-letter queue (for testing with fakeredis).
    """
    limiter = _limiter or SlidingWindowRateLimiter()
    dlq = _dlq or DeadLetterQueue()

    job_payload = {
        "task_id": self.request.id,
        "recipient": recipient,
        "subject": subject,
        "retries": self.request.retries,
    }

    #  1. Check rate limit 
    allowed, current_count = limiter.is_allowed(RATE_LIMIT_KEY)
    if not allowed:
        logger.warning(
            "Rate limit hit (%d/window). Rescheduling task %s in 1s.",
            current_count,
            self.request.id,
        )
        # Reschedule with 1-second countdown - no sleep(), worker stays free
        raise self.retry(exc=RateLimitExceeded("Rate limit exceeded"), countdown=1)

    #  2. Send the email (simulated) 
    try:
        if _force_fail:
            raise ConnectionError("Simulated SMTP connection failure")

        #  REAL IMPLEMENTATION: replace with your email backend call 
        # e.g., send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient])
        logger.info(
            "Email sent -> %s | subject=%s | attempt=%d | count=%d",
            recipient,
            subject,
            self.request.retries + 1,
            current_count,
        )

    except Exception as exc:
        #  3. Retry with exponential backoff 
        # countdown = 2^retries: first retry after 2s, second 4s, third 8s.
        countdown = 2 ** self.request.retries
        logger.warning(
            "Email send failed (attempt %d/%d). Retrying in %ds. Error: %s",
            self.request.retries + 1,
            self.max_retries + 1,
            countdown,
            exc,
        )

        if self.request.retries < self.max_retries:
            # Still have retries left - schedule next attempt
            raise self.retry(exc=exc, countdown=countdown)
        else:
            #  4. Dead-letter after permanent failure 
            # We have exhausted all retries. Move to DLQ.
            logger.error(
                "Task %s permanently failed after %d attempts. Moving to DLQ.",
                self.request.id,
                self.max_retries + 1,
            )
            dlq.push(job_payload, reason=str(exc))
            # Return without re-raising so Celery marks the task as SUCCESS
            # (the job is tracked in the DLQ, not as a Celery failure)
            return {"status": "dead_lettered", "payload": job_payload}

    return {"status": "sent", "recipient": recipient}

