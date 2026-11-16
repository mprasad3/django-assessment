"""
Celery application entry point.

Configuration notes:
- task_acks_late=True  -> task message acknowledged only AFTER the task completes,
                          not when it is received. If the worker is SIGKILL'd while
                          a task is running, the unacknowledged message remains in
                          Redis and will be redelivered to another worker on reconnect.
- task_reject_on_worker_lost=True -> pairs with acks_late; explicitly rejects (not
                                     NACKs) the message so it re-enters the queue
                                     rather than going to a dead-letter exchange.
"""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("django_assessment")

# Load config from Django settings, using the CELERY_ namespace prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in all INSTALLED_APPS
app.autodiscover_tasks()
