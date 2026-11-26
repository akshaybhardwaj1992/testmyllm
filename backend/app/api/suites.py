"""Test suites: a reusable selection of cases from one pinned dataset version."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import bad_request, not_found
from app.models import Dataset, DatasetVersion, TestCase, TestSuite, User
from app.schemas import SuiteIn, SuiteOut
from app.services.access import require_project

router = APIRouter(tags=["suites"])


def suite_case_ids(db: Session, suite: TestSuite) -> list[int]:
    if suite.case_ids is not None:
        return list(suite.case_ids)
    return list(db.scalars(
        select(TestCase.id).where(TestCase.dataset_version_id == suite.dataset_version_id)
        .order_by(TestCase.position, TestCase.id)
    ).all())


def suite_out(db: Session, s: TestSuite) -> SuiteOut:
    v = db.get(DatasetVersion, s.dataset_version_id)
    ds = db.get(Dataset, v.dataset_id)
    count = len(s.case_ids) if s.case_ids is not None else (v.row_count or 0)
    return SuiteOut(
        id=s.id, project_id=s.project_id, name=s.name, description=s.description, dataset_id=ds.id,
        dataset_name=ds.name, dataset_version_id=v.id, version_number=v.version_number,
        is_latest_version=ds.current_version_id == v.id, case_count=count, selection=s.selection,
        tags=list(s.tags or []), created_at=s.created_at,
    )


@router.post("/test-suites", response_model=SuiteOut, status_code=201)
def create_suite(body: SuiteIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, body.project_id, user, "editor")
    v = db.get(DatasetVersion, body.dataset_version_id)
    ds = db.get(Dataset, v.dataset_id) if v else None
    if ds is None or ds.project_id != body.project_id:
        raise not_found("Dataset version")
    case_ids: list[int] | None = None
    if body.selection == "tags":
        case_ids = list(db.scalars(
            select(TestCase.id).where(
                TestCase.dataset_version_id == v.id,
                func.jsonb_exists_any(TestCase.tags.cast(JSONB), body.tags),
            ).order_by(TestCase.position, TestCase.id)
        ).all())
        if not case_ids:
            raise bad_request("No cases in this version carry those tags")
    elif body.selection == "manual":
        valid = set(db.scalars(
            select(TestCase.id).where(TestCase.dataset_version_id == v.id, TestCase.id.in_(body.case_ids))
        ).all())
        stray = [i for i in body.case_ids if i not in valid]
        if stray:
            raise bad_request(f"Case(s) {stray[:10]} are not in dataset version v{v.version_number}")
        case_ids = list(dict.fromkeys(body.case_ids))
    s = TestSuite(
        project_id=body.project_id, name=body.name.strip(), description=body.description,
        dataset_version_id=v.id, case_ids=case_ids, selection=body.selection,
        tags=body.tags if body.selection == "tags" else [],
    )
    db.add(s)
    db.commit()
    return suite_out(db, s)


@router.get("/projects/{project_id}/test-suites", response_model=list[SuiteOut])
def list_suites(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    rows = db.scalars(select(TestSuite).where(TestSuite.project_id == project_id).order_by(TestSuite.created_at.desc()))
    return [suite_out(db, s) for s in rows.all()]


def _suite(db: Session, suite_id: int, user: User, min_role: str = "viewer") -> TestSuite:
    s = db.get(TestSuite, suite_id)
    if s is None:
        raise not_found("Suite")
    require_project(db, s.project_id, user, min_role)
    return s


@router.get("/test-suites/{suite_id}", response_model=SuiteOut)
def get_suite(suite_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return suite_out(db, _suite(db, suite_id, user))


@router.delete("/test-suites/{suite_id}", status_code=204)
def delete_suite(suite_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = _suite(db, suite_id, user, "editor")
    from app.models import TestRun

    db.query(TestRun).filter(TestRun.suite_id == s.id).update({TestRun.suite_id: None})  # runs keep their snapshot
    db.delete(s)
    db.commit()
    return Response(status_code=204)
