"""Schemas for AI security testing (decision D29)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal[
    "prompt_injection", "jailbreak", "indirect_injection", "system_prompt_leakage", "sensitive_disclosure",
    "cross_user_leakage", "excessive_agency",
]
Severity = Literal["critical", "high", "medium", "low"]
Detector = Literal["marker", "canary", "pattern", "cross_user", "agency"]
Technique = Literal["direct", "role_play", "encoding", "multilingual", "multi_turn", "indirect_document",
                    "extraction", "social"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ targets
class IdentityIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", v):
            raise ValueError("Identity names may contain letters, digits, . _ - only")
        return v


class SecurityTargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["model", "rag", "agent"]
    provider_id: int | None = None
    agent_id: int | None = None
    model: str | None = Field(default=None, max_length=200)
    max_tokens: int = Field(default=400, ge=16, le=4000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    system_prompt: str = Field(default="", max_length=20000)
    plant_canary: bool = True
    connector_id: int | None = None
    identities: list[IdentityIn] = Field(default_factory=list, max_length=5)
    rate_limit_rpm: int = Field(default=30, ge=1, le=600)
    authorized: bool = Field(description="I am authorized to security-test this target")
    authorization_note: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def _valid(self) -> SecurityTargetIn:
        if not self.authorized:
            raise ValueError("Confirm that you are authorized to test this target")
        if self.kind == "model" and (not self.provider_id or not (self.model or "").strip()):
            raise ValueError("A model target needs a provider and a model id")
        if self.kind == "rag" and not self.connector_id:
            raise ValueError("A RAG target needs a connector")
        if self.kind == "agent" and not self.agent_id:
            raise ValueError("An agent target needs an agent (Agent Testing → Agents)")
        names = [i.name for i in self.identities]
        if len(names) != len(set(names)):
            raise ValueError("Identity names must be unique")
        return self


class CanaryOut(ORM):
    value: str
    location: str


class SecurityTargetOut(ORM):
    id: int
    project_id: int
    name: str
    kind: str
    provider_id: int | None
    model: str | None
    params: dict
    system_prompt: str
    connector_id: int | None
    connector_name: str | None = None
    agent_id: int | None = None
    agent_name: str | None = None
    identities: list[dict]
    rate_limit_rpm: int
    authorized_at: datetime | None
    authorized_by_email: str | None = None
    authorization_note: str
    canaries: list[CanaryOut] = []
    created_at: datetime


class TargetProbeOut(BaseModel):
    ok: bool
    reply: str | None = None
    latency_ms: int | None = None
    error: str | None = None


# ------------------------------------------------------------------ attacks
class AttackIn(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    category: Category
    technique: Technique = "direct"
    severity: Severity = "medium"
    owasp: str | None = Field(default=None, pattern=r"^LLM(0[1-9]|10)$")
    turns: list[str] = Field(min_length=1, max_length=6)
    document: str | None = Field(default=None, max_length=10000)
    detector: Detector | None = None
    marker: str | None = Field(default=None, min_length=6, max_length=60)
    remediation: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _valid(self) -> AttackIn:
        if any(not t.strip() for t in self.turns):
            raise ValueError("Turns cannot be empty")
        det = self.detector or ("marker" if self.marker else "canary")
        if det == "marker" and not self.marker:
            raise ValueError(f"'{self.key}': the marker detector needs a marker (code word)")
        if self.marker and not any(self.marker in t for t in self.turns) and self.marker not in (self.document or ""):
            raise ValueError(f"'{self.key}': the marker must appear in a turn or the document")
        if self.category == "cross_user_leakage" and not any("{other_user}" in t for t in self.turns):
            raise ValueError(f"'{self.key}': cross-user attacks must reference {{other_user}}")
        if self.category == "indirect_injection" and not self.document:
            raise ValueError(f"'{self.key}': indirect injection needs a poisoned document")
        return self


class AttackOut(ORM):
    id: int
    key: str
    name: str
    category: str
    technique: str
    severity: str
    owasp: str
    turns: list[str]
    document: str | None
    detector: str
    marker: str | None
    remediation: str


class AttackSetIn(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    attacks: list[AttackIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique(self) -> AttackSetIn:
        keys = [a.key for a in self.attacks]
        if len(keys) != len(set(keys)):
            raise ValueError("Attack keys must be unique within a set")
        return self


class AttackSetOut(ORM):
    id: int
    project_id: int | None
    key: str
    name: str
    version: str
    description: str
    builtin: bool
    status: str = "ready"
    generation: dict = {}
    attacks: list[AttackOut]
    created_at: datetime


class EnhancerOut(BaseModel):
    key: str
    label: str
    technique: str
    needs_llm: bool
    description: str


class DeepTeamIn(BaseModel):
    project_id: int
    enhancers: list[str] = Field(min_length=1, max_length=10)
    judge_config_id: int | None = None
    max_variants: int = Field(default=30, ge=1, le=60)

    @field_validator("enhancers")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        from app.security.deepteam_engine import ENHANCERS

        unknown = [e for e in v if e not in ENHANCERS]
        if unknown:
            raise ValueError(f"Unknown enhancer(s): {', '.join(unknown)}")
        return list(dict.fromkeys(v))


class DeepTeamEstimateOut(BaseModel):
    variants_max: int
    llm_rewrites: int
    local_variants: int
    skipped_multi_turn: int
    usd_typical: float | None
    usd_max: float | None
    warnings: list[str]


# ------------------------------------------------------------------ runs & reports
class SecurityRunIn(BaseModel):
    project_id: int
    target_id: int
    attack_set_ids: list[int] = Field(min_length=1, max_length=20)
    categories: list[Category] | None = None
    attempts: int = Field(default=2, ge=1, le=5)
    judge_config_id: int | None = None
    budget_usd: float | None = Field(default=None, gt=0)
    run_name: str | None = Field(default=None, max_length=300)
    documents_planted: bool = False


class SkippedAttack(BaseModel):
    attack: str
    category: str
    reason: str


class SecurityEstimateOut(BaseModel):
    attacks: int
    skipped: list[SkippedAttack]
    target_calls: int
    judge_calls_max: int
    total_usd: float | None
    warnings: list[str]


class SecurityCategoryOut(BaseModel):
    category: str
    label: str
    owasp: str
    attacks: int
    breached: int
    errors: int
    asr: float | None  # attacks with >=1 successful attempt / attacks evaluated
    attempt_asr: float | None  # successful attempts / scored attempts


class SecurityFindingOut(ORM):
    id: int
    test_result_id: int
    attack_name: str
    category: str
    technique: str
    severity: str
    owasp: str
    detector: str
    evidence: str
    successes: int
    attempts: int
    remediation: str


class SecurityReportOut(BaseModel):
    run_id: int
    target: dict
    categories: list[SecurityCategoryOut]
    attacks: int
    breached: int
    asr: float | None
    severity_counts: dict[str, int]
    findings: list[SecurityFindingOut]
    skipped: list[SkippedAttack]
    owasp_version: str


class SecurityGateRule(BaseModel):
    category: Category | Literal["*"]
    max_asr: float = Field(ge=0, le=1)


class SecurityGateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rules: list[SecurityGateRule] = Field(default_factory=list, max_length=10)
    max_critical: int | None = Field(default=0, ge=0)
    max_high: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _valid(self) -> SecurityGateIn:
        cats = [r.category for r in self.rules]
        if len(cats) != len(set(cats)):
            raise ValueError("One rule per category")
        if not self.rules and self.max_critical is None and self.max_high is None:
            raise ValueError("A security gate needs at least one check")
        return self


class SecurityGateOut(ORM):
    id: int
    project_id: int
    name: str
    rules: list[SecurityGateRule]
    max_critical: int | None
    max_high: int | None
    created_at: datetime


class SecurityGateEvaluateIn(BaseModel):
    run_id: int
    record: bool = True
