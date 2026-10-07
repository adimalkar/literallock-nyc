"""Shared LiteralLock contracts. No network or configuration side effects."""
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")


class InspectionGroup(Record):
    group_id: str
    camis: str
    inspection_date_key: str | None = None
    inspection_type: str | None = None
    dba: str = ""
    title: str = ""
    body: str = ""
    raw_row_ids: list[str] = Field(default_factory=list)
    scope_status: str = "valid"
    coverage: Any = "unknown"


class QuerySpec(Record):
    question: str
    mode: str = "conceptual"
    camis: str | None = None
    inspection_date_key: str | None = None
    inspection_type: str | None = None
    borough: str | None = None
    routing_profile: str = "conceptual"
    lexical_weight: float = 0.3
    semantic_weight: float = 0.7
    requirements: list[str] = Field(default_factory=lambda: ["findings", "coverage"])


class SearchHit(Record):
    group_id: str
    rank: int
    score: float | None = None
    group: InspectionGroup


class Excerpt(BaseModel):
    source_id: str
    quote: str


class Claim(BaseModel):
    text: str
    camis: str
    inspection_date_key: str
    inspection_type: str | None = None
    source_ids: list[str] = Field(min_length=1)
    excerpts: list[Excerpt] = Field(min_length=1)


class Assessment(BaseModel):
    requirement_id: str
    status: Literal["supported", "missing", "unknown", "conflicting"]
    source_ids: list[str] = Field(default_factory=list)
    reason: str


class ModelAnswer(BaseModel):
    assessments: list[Assessment]
    claims: list[Claim] = Field(max_length=4)
    gaps: list[str] = Field(max_length=4)
    overall_status: Literal["supported", "partial", "insufficient", "conflicting"]


class RunResult(Record):
    query: QuerySpec
    snapshot: dict = Field(default_factory=dict)
    lexical: list[SearchHit] = Field(default_factory=list)
    semantic: list[SearchHit] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    evidence: list[InspectionGroup] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    status: str = "insufficient"
    counters: dict = Field(default_factory=lambda: {"elastic_logical": 0, "recovery_logical": 0, "recovery_rounds": 0, "mistral_chat": 0})
    timings: dict = Field(default_factory=dict)
