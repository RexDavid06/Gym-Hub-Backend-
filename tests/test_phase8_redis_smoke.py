import os

import pytest

from config.celery import app
from notifications.tasks import celery_ping

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REDIS_SMOKE") != "1",
    reason="Set RUN_REDIS_SMOKE=1 with a live Redis broker and Celery worker to run.",
)


def test_live_redis_worker_executes_a_celery_task():
    assert app.conf.task_always_eager is False

    result = celery_ping.delay()

    assert result.get(timeout=30) == "pong"
