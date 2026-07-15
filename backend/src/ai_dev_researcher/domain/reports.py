from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchClaim(BaseModel):
    id: str
    statement: str
    citation_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


class ReportSection(BaseModel):
    heading: str
    claims: list[ResearchClaim]


class DisagreementSide(BaseModel):
    position: str
    citation_ids: list[str] = Field(min_length=1)


class Disagreement(BaseModel):
    topic: str
    claim_ids: list[str] = Field(min_length=1)
    sides: list[DisagreementSide] = Field(min_length=2)


class ResearchReport(BaseModel):
    title: str
    executive_summary_claim_ids: list[str] = Field(min_length=1)
    sections: list[ReportSection]
    disagreements: list[Disagreement] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    recommendations: list[ResearchClaim]
