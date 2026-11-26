"""Celery tasks: one task per test result (spec §5.2) and restart recovery (spec §5.7)."""
import logging
from datetime import UTC, datetime, timedelta

from celery.signals import worker_ready
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.db import session_factory
from app.core.redact import install_log_redaction
from app.models import TestResult, TestRun
from app.workers.celery_app import celery

log = logging.getLogger(__name__)
install_log_redaction()


@celery.task(
    name="testlab.evaluate_result",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=5,
)
def evaluate_result(result_id: int) -> None:
    from app.services.execution import process_unit

    process_unit(result_id)


@celery.task(name="testlab.purge_expired")
def purge_expired() -> dict:
    """Delete no-persistence runs past their expiry and runs older than a project's retention (D30)."""
    from app.services.retention import purge

    with session_factory()() as db:
        return purge(db)


@celery.task(name="testlab.generate_attacks")
def generate_attacks(set_id: int) -> None:
    """DeepTeam attack-set expansion (D29). Idempotent: replaces the set's attacks on re-run."""
    from app.security.deepteam_engine import run_job

    run_job(set_id)


def requeue_stale(min_age_s: int = 30) -> int:
    """Re-enqueue units of active runs that were in flight when a worker died.

    Completed metric rows are kept and skipped on re-execution, so nothing is redone or duplicated.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=min_age_s)
    with session_factory()() as db:
        ids = db.scalars(
            select(TestResult.id)
            .join(TestRun, TestRun.id == TestResult.run_id)
            .where(
                TestRun.status.in_(("queued", "running")),
                TestResult.status.in_(("pending", "running")),
                TestResult.updated_at < cutoff,
            )
        ).all()
    for rid in ids:
        evaluate_result.delay(rid)
    from app.models import AttackSet

    with session_factory()() as db:
        sets = db.scalars(select(AttackSet.id).where(AttackSet.status == "generating")).all()
    for sid in sets:
        generate_attacks.delay(sid)
    if ids:
        log.warning("re-enqueued %d unfinished unit(s) after worker start", len(ids))
    return len(ids)


@worker_ready.connect
def _on_ready(**_):
    try:
        requeue_stale()
    except Exception:  # noqa: BLE001 - recovery must never stop the worker starting
        log.exception("stale-unit recovery failed")
    try:
        purge_expired.delay()
    except Exception:  # noqa: BLE001
        log.exception("could not schedule the retention purge")
