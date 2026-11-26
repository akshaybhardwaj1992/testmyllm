"""Schemas for agent testing (decision D31)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import CostLine, MetricSelection

TOOL_NAME = r"^[A-Za-z0-9_-]{1,64}$"
KEY = r"^[a-z0-9][a-z0-9-]*$"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MockRule(BaseModel):
    when: dict[str, Any] = Field(default_factory=dict)
    response: str = Field(max_length=5000)


class ToolSpec(BaseModel):
    name: str = Field(pattern=TOOL_NAME)
    description: str = Field(default="", max_length=1000)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    side_effect: bool = False
    response: str = Field(default="OK", max_length=5000)
    rules: list[MockRule] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _schema(self) -> ToolSpec:
        if self.parameters.get("type") != "object":
            raise ValueError(f"Tool '{self.name}': parameters must be a JSON schema with type 'object'")
        return self


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_id: int
    model: str = Field(min_length=1, max_length=200)
    system_prompt: str = Field(default="", max_length=20000)
    tools: list[ToolSpec] = Field(default_factory=list, max_length=20)
    max_steps: int = Field(default=6, ge=1, le=15)
    max_tokens: int = Field(default=500, ge=16, le=4000)
    temperature: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def _unique(self) -> AgentIn:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("Tool names must be unique")
        return self


class AgentOut(ORM):
    id: int
    project_id: int
    name: str
    provider_id: int
    model: str
    system_prompt: str
    tools: list[dict]
    max_steps: int
    params: dict
    created_at: datetime


class AgentTryIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AgentTryOut(BaseModel):
    ok: bool
    final: str | None = None
    trace: dict | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    error: str | None = None


class CallExpectation(BaseModel):
    tool: str = Field(pattern=TOOL_NAME)
    args: dict[str, Any] | None = None


class Expectations(BaseModel):
    must_call: list[CallExpectation] = Field(default_factory=list, max_length=20)
    must_not_call: list[str] = Field(default_factory=list, max_length=20)
    order: list[str] = Field(default_factory=list, max_length=20)
    max_tool_calls: int | None = Field(default=None, ge=0, le=100)
    no_side_effects: bool = False
    answer_contains: list[str] = Field(default_factory=list, max_length=20)
    answer_not_contains: list[str] = Field(default_factory=list, max_length=20)


class ScenarioIn(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=KEY)
    name: str = Field(min_length=1, max_length=200)
    turns: list[str] = Field(min_length=1, max_length=6)
    reference: str | None = Field(default=None, max_length=5000)
    criteria: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    expectations: Expectations = Field(default_factory=Expectations)

    @model_validator(mode="after")
    def _turns(self) -> ScenarioIn:
        if any(not t.strip() for t in self.turns):
            raise ValueError(f"'{self.key}': turns cannot be empty")
        return self


class ScenarioSetIn(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=KEY)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique(self) -> ScenarioSetIn:
        keys = [s.key for s in self.scenarios]
        if len(keys) != len(set(keys)):
            raise ValueError("Scenario keys must be unique within a set")
        return self


class ScenarioSetOut(ORM):
    id: int
    project_id: int
    key: str
    name: str
    version: int
    description: str
    scenarios: list[dict]
    created_at: datetime


class AgentRunIn(BaseModel):
    project_id: int
    agent_ids: list[int] = Field(min_length=1, max_length=5)
    scenario_set_id: int
    metrics: list[MetricSelection] = Field(min_length=1, max_length=30)
    judge_config_id: int
    repeats: int = Field(default=1, ge=1, le=5)
    use_cache: bool = True
    budget_usd: float | None = Field(default=None, gt=0)
    run_name: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _unique(self) -> AgentRunIn:
        if len(set(self.agent_ids)) != len(self.agent_ids):
            raise ValueError("Each agent once")
        return self


class AgentEstimateOut(BaseModel):
    scenarios: int
    agents: int
    agent_calls: int
    agents_cost: CostLine
    judge: CostLine
    total_usd: float | None
    judge_calls: int
    not_applicable: list[str]
    warnings: list[str]
