"""Project-level authorization (owner > editor > viewer), including API-token scoping."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import forbidden, not_found
from app.models import Project, ProjectMember, User

RANK = {"viewer": 0, "editor": 1, "owner": 2}


def role_in(db: Session, project_id: int, user: User) -> str | None:
    role = db.scalar(
        select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    )
    scope = getattr(user, "_token_scope", None)
    if role and scope:
        if scope[0] != project_id:
            return None  # a token never sees other projects
        role = min(role, scope[1], key=RANK.__getitem__)  # capped at the token's role
    return role


def require_project(db: Session, project_id: int, user: User, min_role: str = "viewer") -> tuple[Project, str]:
    """Return (project, role). Non-members get 404 so project ids are not enumerable."""
    project = db.get(Project, project_id)
    role = role_in(db, project_id, user) if project else None
    if project is None or role is None:
        raise not_found("Project")
    if RANK[role] < RANK[min_role]:
        raise forbidden(f"This action needs the {min_role} role on the project")
    return project, role
