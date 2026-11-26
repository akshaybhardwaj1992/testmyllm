from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, token_scope
from app.core.db import get_db
from app.core.errors import ApiError, not_found
from app.core.security import hash_password
from app.models import Project, ProjectMember, TestRun, User
from app.schemas import LastRunSummary, MemberIn, MemberOut, MemberPatch, ProjectIn, ProjectOut, ProjectPatch, ProjectSettings
from app.services.access import require_project, role_in

router = APIRouter(prefix="/projects", tags=["projects"])


def _out(db: Session, project: Project, role: str) -> ProjectOut:
    last = db.scalar(
        select(TestRun).where(TestRun.project_id == project.id).order_by(TestRun.created_at.desc()).limit(1)
    )
    summary = None
    if last is not None:
        decided = last.passed_tests + last.failed_tests + last.error_tests
        summary = LastRunSummary(
            id=last.id,
            run_name=last.run_name,
            status=last.status,
            pass_rate=(last.passed_tests / decided) if decided else None,
            created_at=last.created_at,
        )
    return ProjectOut(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at, role=role, last_run=summary,
        settings=ProjectSettings(**{k: v for k, v in (project.settings or {}).items() if k in ProjectSettings.model_fields}),
    )


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if token_scope(user):
        raise ApiError(403, "forbidden", "API tokens cannot create projects")
    p = Project(name=body.name.strip(), description=body.description, created_by=user.id, settings={})
    p.members.append(ProjectMember(user_id=user.id, role="owner"))
    db.add(p)
    db.commit()
    return _out(db, p, "owner")


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(
        select(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    ).all()
    scope = token_scope(user)
    return [_out(db, p, role_in(db, p.id, user)) for p, _ in rows if not scope or p.id == scope[0]]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p, role = require_project(db, project_id, user)
    return _out(db, p, role)


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: int, body: ProjectPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    p, role = require_project(db, project_id, user, "owner")
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description
    if body.settings is not None:
        p.settings = {**(p.settings or {}), **body.settings.model_dump()}
    db.commit()
    return _out(db, p, role)


@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    rows = db.execute(
        select(ProjectMember, User.email).join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id).order_by(User.email)
    ).all()
    return [MemberOut(user_id=m.user_id, email=email, role=m.role) for m, email in rows]


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    project_id: int, body: MemberIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    """Add an existing user, or create one with `initial_password` (single-organization MVP, decision D13)."""
    require_project(db, project_id, user, "owner")
    email = body.email.lower()
    target = db.scalar(select(User).where(User.email == email))
    created = False
    if target is None:
        if not body.initial_password:
            raise ApiError(
                400, "user_not_found",
                "No user with that email. Provide an initial password to create the account.",
            )
        target = User(email=email, password_hash=hash_password(body.initial_password))
        db.add(target)
        db.flush()
        created = True
    if db.get(ProjectMember, (project_id, target.id)):
        raise ApiError(409, "already_member", f"{email} is already a member")
    db.add(ProjectMember(project_id=project_id, user_id=target.id, role=body.role))
    db.commit()
    return MemberOut(user_id=target.id, email=email, role=body.role, created_user=created)


def _owner_count(db: Session, project_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(ProjectMember)
        .where(ProjectMember.project_id == project_id, ProjectMember.role == "owner")
    ) or 0


@router.patch("/{project_id}/members/{user_id}", response_model=MemberOut)
def change_role(
    project_id: int, user_id: int, body: MemberPatch, db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    require_project(db, project_id, user, "owner")
    m = db.get(ProjectMember, (project_id, user_id))
    if m is None:
        raise not_found("Member")
    if m.role == "owner" and body.role != "owner" and _owner_count(db, project_id) <= 1:
        raise ApiError(409, "last_owner", "A project needs at least one owner")
    m.role = body.role
    db.commit()
    return MemberOut(user_id=user_id, email=db.get(User, user_id).email, role=m.role)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
def remove_member(project_id: int, user_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user, "owner")
    m = db.get(ProjectMember, (project_id, user_id))
    if m is None:
        raise not_found("Member")
    if m.role == "owner" and _owner_count(db, project_id) <= 1:
        raise ApiError(409, "last_owner", "A project needs at least one owner")
    db.delete(m)
    db.commit()
    return Response(status_code=204)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p, _ = require_project(db, project_id, user, "owner")
    db.delete(p)
    db.commit()
    return Response(status_code=204)
