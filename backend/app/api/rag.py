"""RAG connectors: configure and test a user's RAG endpoint (SSRF-protected, auth write-only)."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import not_found
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.models import RagConnector, User
from app.rag.connector import ConnectorConfig, ConnectorError, call
from app.schemas import ConnectorTestIn, ConnectorTestOut, RagConnectorIn, RagConnectorOut, RagConnectorPatch
from app.services.access import require_project

router = APIRouter(tags=["rag"])


def config_of(c: RagConnector) -> ConnectorConfig:
    m = c.response_mapping or {}
    return ConnectorConfig(
        endpoint_url=c.endpoint_url, method=c.method, auth_header_name=c.auth_header_name,
        auth_value=decrypt_secret(c.encrypted_auth) if c.encrypted_auth else "",
        request_template=c.request_template, answer_path=m.get("answer_path", "answer"),
        contexts_path=m.get("contexts_path", ""), context_text_path=m.get("context_text_path", ""),
        timeout_s=c.timeout_s,
    )


def _apply(c: RagConnector, body: RagConnectorIn) -> None:
    c.name = body.name.strip()
    c.endpoint_url = body.endpoint_url.strip()
    c.method = body.method
    c.auth_header_name = body.auth_header_name.strip()
    c.request_template = body.request_template
    c.response_mapping = body.response_mapping.model_dump()
    c.timeout_s = body.timeout_s
    if body.auth_value:
        c.encrypted_auth, c.auth_hint = encrypt_secret(body.auth_value), mask_secret(body.auth_value)


def _connector(db: Session, connector_id: int, user: User, min_role: str = "viewer") -> RagConnector:
    c = db.get(RagConnector, connector_id)
    if c is None:
        raise not_found("RAG connector")
    require_project(db, c.project_id, user, min_role)
    return c


@router.post("/projects/{project_id}/rag-connectors", response_model=RagConnectorOut, status_code=201)
def create_connector(
    project_id: int, body: RagConnectorIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    require_project(db, project_id, user, "editor")
    c = RagConnector(project_id=project_id)
    _apply(c, body)
    db.add(c)
    db.commit()
    return c


@router.get("/projects/{project_id}/rag-connectors", response_model=list[RagConnectorOut])
def list_connectors(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    return db.scalars(select(RagConnector).where(RagConnector.project_id == project_id).order_by(RagConnector.id)).all()


@router.put("/rag-connectors/{connector_id}", response_model=RagConnectorOut)
def update_connector(
    connector_id: int, body: RagConnectorPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    c = _connector(db, connector_id, user, "editor")
    _apply(c, body)
    if body.clear_auth:
        c.encrypted_auth, c.auth_hint = "", ""
    db.commit()
    return c


@router.delete("/rag-connectors/{connector_id}", status_code=204)
def delete_connector(connector_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = _connector(db, connector_id, user, "editor")
    db.delete(c)
    db.commit()
    return Response(status_code=204)


@router.post("/rag-connectors/test", response_model=ConnectorTestOut)
async def test_connector(body: ConnectorTestIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Call the endpoint once with a sample question and show the parsed answer and contexts."""
    require_project(db, body.project_id, user, "editor")
    auth_value = body.auth_value or ""
    if body.connector_id and not auth_value:
        stored = _connector(db, body.connector_id, user, "editor")
        if stored.project_id != body.project_id:
            raise not_found("RAG connector")
        auth_value = decrypt_secret(stored.encrypted_auth) if stored.encrypted_auth else ""
    m = body.response_mapping
    cfg = ConnectorConfig(
        endpoint_url=body.endpoint_url.strip(), method=body.method, auth_header_name=body.auth_header_name,
        auth_value=auth_value, request_template=body.request_template, answer_path=m.answer_path,
        contexts_path=m.contexts_path, context_text_path=m.context_text_path, timeout_s=body.timeout_s,
    )
    try:
        r = await call(cfg, body.question)
    except ConnectorError as e:
        return ConnectorTestOut(ok=False, error=str(e))
    return ConnectorTestOut(
        ok=True, status_code=r.status_code, latency_ms=r.latency_ms, answer=r.answer, contexts=r.contexts,
        warnings=r.warnings, raw_preview=r.raw_preview,
    )
