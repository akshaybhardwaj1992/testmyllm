"""Celery app. Prefork pool, one unit per process at a time, late acks so a killed worker's unit is
redelivered (and the unit itself is idempotent)."""
from celery import Celery

from app.core.config import get_settings

s = get_settings()

celery = Celery("testlab", broker=s.redis_url, backend=None, include=["app.workers.tasks"])
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=s.unit_timeout_s,
    task_time_limit=s.unit_timeout_s + 60,
    broker_transport_options={"visibility_timeout": s.unit_timeout_s + 300},
    task_always_eager=s.celery_eager,
    task_eager_propagates=True,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    # D30: retention + no-persistence cleanup (beat runs embedded in the worker: `worker -B`)
    beat_schedule={"purge-expired": {"task": "testlab.purge_expired", "schedule": 900.0}},
    beat_schedule_filename="/tmp/celerybeat-schedule",
)
