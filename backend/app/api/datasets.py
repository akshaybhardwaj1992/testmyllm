"""Datasets, versions and test cases. Versions are immutable; every edit creates a new version."""
import json

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import ApiError, bad_request, not_found
from app.datasets import parsing
from app.datasets.service import CaseData, add_uploaded_version, create_dataset, derive_version
from app.models import Dataset, DatasetVersion, TestCase, User
from app.schemas import (
    CaseCreate,
    CaseEditOut,
    CaseFullOut,
    CasePatch,
    CasesPage,
    DatasetOut,
    DatasetPatch,
    DatasetVersionOut,
    UploadOut,
    UploadReport,
)
from app.services.access import require_project

router = APIRouter(tags=["datasets"])


def _dataset(db: Session, dataset_id: int, user: User, min_role: str = "viewer") -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise not_found("Dataset")
    require_project(db, ds.project_id, user, min_role)
    return ds


def _version(db: Session, version_id: int, user: User, min_role: str = "viewer") -> DatasetVersion:
    v = db.get(DatasetVersion, version_id)
    if v is None:
        raise not_found("Dataset version")
    _dataset(db, v.dataset_id, user, min_role)
    return v


def dataset_out(db: Session, ds: Dataset) -> DatasetOut:
    cur = db.get(DatasetVersion, ds.current_version_id) if ds.current_version_id else None
    count = db.scalar(select(func.count()).where(DatasetVersion.dataset_id == ds.id)) or 0
    return DatasetOut(
        id=ds.id, project_id=ds.project_id, name=ds.name, created_at=ds.created_at,
        current_version=DatasetVersionOut.model_validate(cur) if cur else None, version_count=count,
    )


# ------------------------------------------------------------------ upload (mapping + validation report)
@router.post("/datasets/upload", response_model=UploadOut)
async def upload(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    name: str | None = Form(None, max_length=200),
    dataset_id: int | None = Form(None),
    mapping: str | None = Form(None, description="JSON object {target: column|null}; omit to use the suggestion"),
    metadata_columns: str | None = Form(None, description="JSON list of columns kept as case metadata"),
    dry_run: bool = Form(True),
    skip_invalid: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Two-step import: call with dry_run=true to get columns, a suggested mapping and the validation
    report; call again with dry_run=false (and the confirmed mapping) to create the dataset version."""
    require_project(db, project_id, user, "editor")
    target_ds = None
    if dataset_id is not None:
        target_ds = _dataset(db, dataset_id, user, "editor")
        if target_ds.project_id != project_id:
            raise bad_request("Dataset belongs to another project")
    data = await file.read(parsing.MAX_BYTES + 1)
    try:
        parsed = parsing.parse_file(file.filename or "upload", data)
    except parsing.ParseError as e:
        raise ApiError(400, "invalid_file", str(e)) from e

    suggested = parsing.suggest_mapping(parsed.columns)
    try:
        used = {t: suggested.get(t) for t in parsing.TARGETS}
        if mapping:
            supplied = json.loads(mapping)
            if not isinstance(supplied, dict):
                raise ValueError
            used = {t: (supplied.get(t) or None) for t in parsing.TARGETS} | {
                k: v for k, v in supplied.items() if k not in parsing.TARGETS}
        meta_cols = json.loads(metadata_columns) if metadata_columns else []
        if not isinstance(meta_cols, list) or not all(isinstance(c, str) for c in meta_cols):
            raise ValueError
    except ValueError as e:  # includes JSONDecodeError
        raise bad_request("mapping must be a JSON object and metadata_columns a JSON list of strings") from e

    problems = parsing.validate_mapping(used, parsed.columns, meta_cols)
    out = UploadOut(
        columns=parsed.columns, suggested_mapping=suggested, mapping_used=used, metadata_columns=meta_cols,
        mapping_problems=problems, report=None, imported=False,
    )
    if problems:
        if not dry_run:
            raise ApiError(400, "invalid_mapping", "Fix the column mapping first", problems)
        return out
    report = parsing.normalize(parsed, used, meta_cols)
    rep = report.as_dict()
    out.report = UploadReport(**rep)
    if dry_run:
        return out

    if not report.valid:
        raise ApiError(400, "no_valid_rows", "No valid rows to import", rep)
    if report.errors and not skip_invalid:
        raise ApiError(
            400, "invalid_rows", f"{report.invalid_rows} row(s) have errors. Fix them or import valid rows only.", rep
        )
    cases = [CaseData.from_normalized(c) for c in report.valid]
    fname = (file.filename or "upload")[:300]
    if target_ds is None:
        ds_name = (name or "").strip() or fname.rsplit(".", 1)[0][:200] or "Dataset"
        target_ds, version = create_dataset(db, project_id, ds_name, cases, user.id, fname)
    else:
        version = add_uploaded_version(db, target_ds, cases, user.id, fname)
    db.commit()
    out.imported = True
    out.dataset = dataset_out(db, target_ds)
    out.version = DatasetVersionOut.model_validate(version)
    return out


# ------------------------------------------------------------------ datasets
@router.get("/projects/{project_id}/datasets", response_model=list[DatasetOut])
def list_datasets(
    project_id: int, q: str | None = Query(None, max_length=200), db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    require_project(db, project_id, user)
    stmt = select(Dataset).where(Dataset.project_id == project_id)
    if q:
        stmt = stmt.where(Dataset.name.ilike(f"%{q.strip()}%"))
    return [dataset_out(db, d) for d in db.scalars(stmt.order_by(Dataset.created_at.desc())).all()]


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return dataset_out(db, _dataset(db, dataset_id, user))


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut)
def rename_dataset(
    dataset_id: int, body: DatasetPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    ds = _dataset(db, dataset_id, user, "editor")
    ds.name = body.name.strip()
    db.commit()
    return dataset_out(db, ds)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    ds = _dataset(db, dataset_id, user, "owner")
    from app.models import TestRun

    used = db.scalar(
        select(func.count()).select_from(TestRun).join(DatasetVersion, DatasetVersion.id == TestRun.dataset_version_id)
        .where(DatasetVersion.dataset_id == ds.id)
    )
    if used:
        raise ApiError(409, "in_use", f"{used} run(s) used this dataset; delete is blocked to keep results reproducible")
    ds.current_version_id = None
    db.flush()
    db.delete(ds)
    db.commit()
    return Response(status_code=204)


@router.get("/datasets/{dataset_id}/versions", response_model=list[DatasetVersionOut])
def list_versions(dataset_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _dataset(db, dataset_id, user)
    return db.scalars(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_number.desc())
    ).all()


@router.get("/dataset-versions/{version_id}/cases", response_model=CasesPage)
def list_cases(
    version_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500),
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=50),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    _version(db, version_id, user)
    cond = [TestCase.dataset_version_id == version_id]
    if q:
        like = f"%{q.strip()}%"
        cond.append(or_(TestCase.input.ilike(like), TestCase.expected_output.ilike(like)))
    if tag:
        cond.append(TestCase.tags.cast(JSONB).contains([tag]))
    total = db.scalar(select(func.count()).select_from(TestCase).where(*cond)) or 0
    items = db.scalars(
        select(TestCase).where(*cond).order_by(TestCase.position, TestCase.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    tags = sorted({
        t for row in db.scalars(select(TestCase.tags).where(TestCase.dataset_version_id == version_id)).all()
        for t in (row or [])
    })
    return CasesPage(
        items=[CaseFullOut.model_validate(c) for c in items], total=total, page=page, page_size=page_size, tags=tags
    )


# ------------------------------------------------------------------ test cases (each edit -> new version)
def _data(body) -> CaseData:
    return CaseData(
        body.input.strip(), body.expected_output, body.recorded_output, body.provided_context, body.criteria,
        body.tags, body.metadata,
    )


def _case(db: Session, case_id: int, user: User, min_role: str = "viewer") -> TestCase:
    c = db.get(TestCase, case_id)
    if c is None or c.dataset_version_id is None:
        raise not_found("Test case")
    _version(db, c.dataset_version_id, user, min_role)
    return c


@router.get("/test-cases/{case_id}", response_model=CaseFullOut)
def get_case(case_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _case(db, case_id, user)


@router.post("/test-cases", response_model=CaseEditOut, status_code=201)
def create_case(body: CaseCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    ds = _dataset(db, body.dataset_id, user, "editor")
    v, _ = derive_version(db, ds, body.base_version_id, user.id, add=[_data(body)], note="Added a case")
    db.commit()
    new_case = db.scalars(
        select(TestCase).where(TestCase.dataset_version_id == v.id).order_by(TestCase.position.desc()).limit(1)
    ).first()
    return CaseEditOut(version=DatasetVersionOut.model_validate(v), case=CaseFullOut.model_validate(new_case))


@router.patch("/test-cases/{case_id}", response_model=CaseEditOut)
def update_case(case_id: int, body: CasePatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = _case(db, case_id, user, "editor")
    ds = db.get(Dataset, db.get(DatasetVersion, c.dataset_version_id).dataset_id)
    v, ids = derive_version(db, ds, c.dataset_version_id, user.id, update={c.id: _data(body)}, note="Edited a case")
    db.commit()
    return CaseEditOut(
        version=DatasetVersionOut.model_validate(v), case=CaseFullOut.model_validate(db.get(TestCase, ids[c.case_key]))
    )


@router.delete("/test-cases/{case_id}", response_model=CaseEditOut)
def delete_case(case_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = _case(db, case_id, user, "editor")
    ds = db.get(Dataset, db.get(DatasetVersion, c.dataset_version_id).dataset_id)
    v, _ = derive_version(db, ds, c.dataset_version_id, user.id, delete={c.id}, note="Deleted a case")
    db.commit()
    return CaseEditOut(version=DatasetVersionOut.model_validate(v), case=None)
