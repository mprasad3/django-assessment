"""
conftest.py - pytest configuration for the assessment project.
Configures Celery to run eagerly (synchronously) during tests.
"""
import os
import django
import pytest

# Force eager mode for all tests via environment / Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")


@pytest.fixture(scope="session", autouse=True)
def configure_celery_eager():
    """
    Force Celery to run tasks synchronously in all tests.
    Must be session-scoped and autouse so it runs before any test.
    """
    from django.conf import settings
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False

    # Also configure the Celery app instance directly
    from celery_app import app
    app.conf.update(
        task_always_eager=True,
        task_eager_propagates=False,
    )

