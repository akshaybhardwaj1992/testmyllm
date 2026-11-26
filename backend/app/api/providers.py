"""Providers (API keys are write-only) and judge configs."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import bad_request, not_found
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.models import JudgeConfig, ModelConfig, Provider, User
from app.providers import openrouter
from app.schemas import (
    JudgeConfigIn,
    JudgeConfigOut,
    JudgeConfigPatch,
    ModelConfigIn,
    ModelConfigOut,
    ProviderIn,
    ProviderOut,
    ProviderPatch,
    ProviderTestOut,
)
from app.services.access import require_project

router = APIRouter(tags=["providers"])


def _provider(db: Session, provider_id: int, user: User, min_role: str) -> Provider:
    p = db.get(Provider, provider_id)
    if p is None:
        raise not_found("Provider")
    require_project(db, p.project_id, user, min_role)
    return p


@router.post("/projects/{project_id}/providers", response_model=ProviderOut, status_code=201)
def create_provider(
    project_id: int, body: ProviderIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    require_project(db, project_id, user, "owner")
    key = body.api_key.strip()
    p = Provider(
        project_id=project_id, name=body.name.strip(), type=body.type,
        encrypted_credentials=encrypt_secret(key), key_hint=mask_secret(key),
    )
    db.add(p)
    db.commit()
    return p


@router.get("/projects/{project_id}/providers", response_model=list[ProviderOut])
def list_providers(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    return db.scalars(select(Provider).where(Provider.project_id == project_id).order_by(Provider.id)).all()


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def patch_provider(
    provider_id: int, body: ProviderPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    p = _provider(db, provider_id, user, "owner")
    if body.name is not None:
        p.name = body.name.strip()
    if body.api_key is not None:
        key = body.api_key.strip()
        p.encrypted_credentials, p.key_hint = encrypt_secret(key), mask_secret(key)
        p.status, p.status_message = "untested", ""
    db.commit()
    return p


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _provider(db, provider_id, user, "owner")
    db.delete(p)
    db.commit()
    return Response(status_code=204)


@router.post("/providers/{provider_id}/test", response_model=ProviderTestOut)
def test_provider(provider_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = _provider(db, provider_id, user, "editor")
    ok, message = openrouter.test_key(decrypt_secret(p.encrypted_credentials))
    p.status, p.status_message = ("ok" if ok else "error"), message
    db.commit()
    return ProviderTestOut(ok=ok, message=message)


# ---------------------------------------------------------------- saved model configs (M4)
def _model_out(m: ModelConfig) -> ModelConfigOut:
    p = m.params or {}
    return ModelConfigOut(
        id=m.id, provider_id=m.provider_id, name=m.name, model=m.model,
        max_tokens=int(p.get("max_tokens") or 1000), temperature=p.get("temperature"), created_at=m.created_at,
    )


@router.get("/projects/{project_id}/model-configs", response_model=list[ModelConfigOut])
def list_model_configs(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    rows = db.scalars(
        select(ModelConfig).join(Provider, Provider.id == ModelConfig.provider_id)
        .where(Provider.project_id == project_id).order_by(ModelConfig.name)
    ).all()
    return [_model_out(m) for m in rows]


@router.post("/projects/{project_id}/model-configs", response_model=ModelConfigOut, status_code=201)
def create_model_config(
    project_id: int, body: ModelConfigIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    require_project(db, project_id, user, "editor")
    prov = db.get(Provider, body.provider_id)
    if prov is None or prov.project_id != project_id:
        raise bad_request("Provider not found in this project")
    m = ModelConfig(
        provider_id=prov.id, name=body.name.strip(), model=body.model.strip(),
        params={"max_tokens": body.max_tokens, "temperature": body.temperature},
    )
    db.add(m)
    db.commit()
    return _model_out(m)


@router.delete("/model-configs/{config_id}", status_code=204)
def delete_model_config(config_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = db.get(ModelConfig, config_id)
    prov = db.get(Provider, m.provider_id) if m else None
    if m is None or prov is None:
        raise not_found("Model config")
    require_project(db, prov.project_id, user, "editor")
    db.delete(m)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- judge configs
def _judge(db: Session, judge_id: int, user: User, min_role: str) -> JudgeConfig:
    j = db.get(JudgeConfig, judge_id)
    if j is None:
        raise not_found("Judge config")
    require_project(db, j.project_id, user, min_role)
    return j


def _clear_default(db: Session, project_id: int, keep_id: int) -> None:
    db.execute(
        update(JudgeConfig)
        .where(JudgeConfig.project_id == project_id, JudgeConfig.id != keep_id)
        .values(is_default=False)
    )


@router.post("/projects/{project_id}/judge-configs", response_model=JudgeConfigOut, status_code=201)
def create_judge(
    project_id: int, body: JudgeConfigIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    require_project(db, project_id, user, "editor")
    prov = db.get(Provider, body.provider_id)
    if prov is None or prov.project_id != project_id:
        raise bad_request("Provider not found in this project")
    first = db.scalar(select(JudgeConfig.id).where(JudgeConfig.project_id == project_id).limit(1)) is None
    j = JudgeConfig(project_id=project_id, **body.model_dump())
    j.is_default = body.is_default or first
    db.add(j)
    db.flush()
    if j.is_default:
        _clear_default(db, project_id, j.id)
    db.commit()
    return j


@router.get("/projects/{project_id}/judge-configs", response_model=list[JudgeConfigOut])
def list_judges(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    return db.scalars(
        select(JudgeConfig).where(JudgeConfig.project_id == project_id).order_by(JudgeConfig.id)
    ).all()


@router.patch("/judge-configs/{judge_id}", response_model=JudgeConfigOut)
def patch_judge(
    judge_id: int, body: JudgeConfigPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    j = _judge(db, judge_id, user, "editor")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(j, k, v)
    if body.is_default:
        _clear_default(db, j.project_id, j.id)
    db.commit()
    return j


@router.delete("/judge-configs/{judge_id}", status_code=204)
def delete_judge(judge_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    j = _judge(db, judge_id, user, "editor")
    db.delete(j)
    db.commit()
    return Response(status_code=204)
