"""Dataset versions are immutable: every upload or edit creates a new version (spec §7)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.datasets.parsing import NormalizedCase
from app.models import Dataset, DatasetVersion, TestCase


@dataclass
class CaseData:
    input: str
    expected_output: str | None = None
    recorded_output: str | None = None
    provided_context: list[str] | None = None
    criteria: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_normalized(cls, c: NormalizedCase) -> CaseData:
        return cls(c.input, c.expected_output, c.response, c.context, c.criteria, c.tags, c.metadata)

    @classmethod
    def from_case(cls, c: TestCase) -> CaseData:
        return cls(
            c.input, c.expected_output, c.recorded_output, c.provided_context, c.criteria,
            list(c.tags or []), dict(c.case_metadata or {}),
        )


def fields_present(cases: list[CaseData]) -> list[str]:
    present = set()
    for c in cases:
        if c.expected_output:
            present.add("reference")
        if c.recorded_output:
            present.add("response")
        if c.provided_context:
            present.add("context")
        if c.criteria:
            present.add("criteria")
    return sorted(present)


def _new_version(
    db: Session, dataset: Dataset, rows: list[tuple[str | None, CaseData]], user_id: int,
    source_filename: str = "", note: str = "",
) -> DatasetVersion:
    """rows: (case_key or None for new cases, data). Sets the dataset's current version."""
    number = (db.scalar(select(func.max(DatasetVersion.version_number)).where(
        DatasetVersion.dataset_id == dataset.id)) or 0) + 1
    version = DatasetVersion(
        dataset_id=dataset.id, version_number=number, row_count=len(rows), source_filename=source_filename,
        fields_present=fields_present([d for _, d in rows]), change_note=note[:300], created_by=user_id,
    )
    db.add(version)
    db.flush()
    for pos, (key, d) in enumerate(rows):
        tc = TestCase(
            dataset_version_id=version.id, project_id=dataset.project_id, position=pos, input=d.input,
            expected_output=d.expected_output, recorded_output=d.recorded_output,
            provided_context=d.provided_context, criteria=d.criteria, tags=d.tags, case_metadata=d.metadata,
        )
        if key:
            tc.case_key = key
        db.add(tc)
    dataset.current_version_id = version.id
    db.flush()
    return version


def create_dataset(
    db: Session, project_id: int, name: str, cases: list[CaseData], user_id: int, filename: str
) -> tuple[Dataset, DatasetVersion]:
    ds = Dataset(project_id=project_id, name=name)
    db.add(ds)
    db.flush()
    v = _new_version(db, ds, [(None, c) for c in cases], user_id, filename, "Initial upload")
    return ds, v


def add_uploaded_version(
    db: Session, dataset: Dataset, cases: list[CaseData], user_id: int, filename: str
) -> DatasetVersion:
    """A re-upload replaces the case list wholesale (new case keys)."""
    return _new_version(db, dataset, [(None, c) for c in cases], user_id, filename, f"Uploaded {filename}")


def derive_version(
    db: Session,
    dataset: Dataset,
    base_version_id: int,
    user_id: int,
    add: list[CaseData] | None = None,
    update: dict[int, CaseData] | None = None,
    delete: set[int] | None = None,
    note: str = "",
) -> tuple[DatasetVersion, dict[str, int]]:
    """Copy the base version applying edits. Returns the new version and {case_key: new_case_id}.

    Only the current version can be edited (409 otherwise) so two editors cannot silently fork.
    """
    if dataset.current_version_id != base_version_id:
        raise ApiError(
            409, "version_conflict",
            "This dataset has a newer version. Reload and apply your change to the latest version.",
        )
    update, delete = update or {}, delete or set()
    base_cases = db.scalars(
        select(TestCase).where(TestCase.dataset_version_id == base_version_id).order_by(TestCase.position, TestCase.id)
    ).all()
    ids = {c.id for c in base_cases}
    missing = (set(update) | delete) - ids
    if missing:
        raise ApiError(400, "bad_request", f"Case(s) {sorted(missing)} are not in version {base_version_id}")
    rows: list[tuple[str | None, CaseData]] = []
    for c in base_cases:
        if c.id in delete:
            continue
        rows.append((c.case_key, update.get(c.id) or CaseData.from_case(c)))
    rows.extend((None, d) for d in (add or []))
    if not rows:
        raise ApiError(400, "bad_request", "A dataset version must contain at least one case")
    v = _new_version(db, dataset, rows, user_id, note=note)
    new_ids = {
        c.case_key: c.id
        for c in db.scalars(select(TestCase).where(TestCase.dataset_version_id == v.id)).all()
    }
    return v, new_ids
