"""Versioned, public research arguments; never hidden model reasoning."""
from typing import Literal
from pydantic import Field
from .agent_models import Strict


class Condition(Strict):
    description: str = Field(min_length=1, max_length=500)
    observable: str = Field(min_length=1, max_length=300)
    verification_method: str = Field(min_length=1, max_length=300)
    time_window: str = Field(min_length=1, max_length=200)
    required_data: list[str] = Field(min_length=1, max_length=6)


class Thesis(Strict):
    thesis_id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,59}$')
    subject: str
    statement: str = Field(min_length=1, max_length=700)
    claim_type: Literal['fact', 'derived', 'inference', 'hypothesis']
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    reasoning: str = Field(min_length=1, max_length=900)
    assumptions: list[str] = Field(max_length=5)
    invalidation_conditions: list[Condition] = Field(min_length=1, max_length=3)
    limitations: list[str] = Field(max_length=6)
    status: Literal['supported', 'conditional', 'unresolved']


class ThesisSet(Strict):
    theses: list[Thesis] = Field(max_length=3)
    gaps: list[str] = Field(max_length=10)


class Rebuttal(Strict):
    target_thesis_id: str
    reason_type: Literal['fact_error', 'relevance', 'timing', 'inference', 'assumption', 'counterevidence']
    explanation: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(max_length=8)


class Lookup(Strict):
    kind: Literal['fetch_market', 'fetch_sector', 'search_policy', 'fetch_document', 'search_announcement', 'fetch_global']
    source_id: str = ''
    subjects: list[str] = Field(max_length=15)
    query_terms: list[str] = Field(default_factory=list, max_length=8)
    url: str = ''
    expected_effect: str = Field(min_length=1, max_length=500)


class Dispute(Strict):
    dispute_id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,59}$')
    thesis_ids: list[str] = Field(min_length=1, max_length=6)
    question: str = Field(min_length=1, max_length=600)
    materiality: str = Field(min_length=1, max_length=500)
    required_fields: list[str] = Field(min_length=1, max_length=8)
    lookups: list[Lookup] = Field(max_length=2)


class Debate(Strict):
    rebuttals: list[Rebuttal] = Field(max_length=6)
    disputes: list[Dispute] = Field(max_length=2)
    gaps: list[str] = Field(max_length=10)


class Resolution(Strict):
    dispute_id: str
    resolution: Literal['bull_supported', 'bear_supported', 'insufficient', 'conflicting', 'not_material']
    reason: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(max_length=8)


class Verdict(Strict):
    stance: Literal['偏积极', '中性观察', '偏谨慎', '暂不形成判断']
    sufficiency: Literal['充分', '受限', '不足']
    accepted_thesis_ids: list[str] = Field(max_length=6)
    rejected_thesis_ids: list[str] = Field(max_length=6)
    decision_basis: str = Field(min_length=1, max_length=1400)
    evidence_ids: list[str] = Field(max_length=16)
    resolutions: list[Resolution] = Field(max_length=2)
    observation_conditions: list[Condition] = Field(max_length=4)
    invalidation_conditions: list[Condition] = Field(max_length=4)
    limitations: list[str] = Field(max_length=12)


class RiskReview(Strict):
    approved: bool
    reasons: list[str] = Field(min_length=1, max_length=10)
    evidence_ids: list[str] = Field(max_length=16)
    limitations: list[str] = Field(max_length=10)
