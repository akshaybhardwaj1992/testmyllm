"""Retention and no-persistence cleanup (spec §11, decision D30).

- No-persistence runs carry `expires_at`; once past it they are deleted with everything they produced.
- A project's `retention_days` deletes finished runs older than that. Runs saved as baselines are kept
  (a baseline is an explicit decision to keep a run), and so are runs still in progress.

Deleting a run cascades to its results, metric rows, human reviews, findings, gate evaluations and
export records; export files on disk and ad-hoc cases (Studio / security attacks) are removed here.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.models import Baseline, Export, Project, TestCase, TestResult, TestRun

log = logging.getLogger(__name__)
TERMINAL = ("completed", "failed", "cancelled")


def expired_run_ids(db: Session, now: datetime) -> tuple[list[int], list[int]]:
    """(no-persistence runs past expiry, runs past their project's retention)."""
    ephemeral = list(db.scalars(select(TestRun.id).where(TestRun.expires_at.is_not(None), TestRun.expires_at <= now)))
    retained: list[int] = []
    for p in db.scalars(select(Project)).all():
        days = (p.settings or {}).get("retention_days")
        if not days:
            continue
        cutoff = now - timedelta(days=int(days))
        retained += db.scalars(select(TestRun.id).where(
            TestRun.project_id == p.id, TestRun.created_at < cutoff, TestRun.status.in_(TERMINAL),
            ~exists().where(Baseline.run_id == TestRun.id),
        )).all()
    return ephemeral, [r for r in retained if r not in set(ephemeral)]


def delete_runs(db: Session, run_ids: list[int]) -> int:
    if not run_ids:
        return 0
    case_ids = set(db.scalars(
        select(TestResult.test_case_id).join(TestCase, TestCase.id == TestResult.test_case_id)
        .where(TestResult.run_id.in_(run_ids), TestCase.dataset_version_id.is_(None))
    ).all())
    for path in db.scalars(select(Export.path).where(Export.run_id.in_(run_ids))).all():
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as e:  # never block the purge on a file problem
            log.warning("could not delete export file %s: %s", path, e)
    n = db.execute(delete(TestRun).where(TestRun.id.in_(run_ids))).rowcount
    if case_ids:  # ad-hoc cases no longer used by any run
        db.execute(delete(TestCase).where(
            TestCase.id.in_(case_ids), ~exists().where(TestResult.test_case_id == TestCase.id)))
    db.commit()
    return n


def purge(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    ephemeral, retained = expired_run_ids(db, now)
    out = {"no_persistence": delete_runs(db, ephemeral), "retention": delete_runs(db, retained)}
    if any(out.values()):
        log.warning("purged runs: %s", out)
    return out
