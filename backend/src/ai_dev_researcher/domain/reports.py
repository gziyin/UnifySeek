from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ResearchClaim(BaseModel):
    id: str
    statement: str
    citation_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


class ReportTable(BaseModel):
    """结构化对比表格。rows 每行长度必须与 columns 一致（结构自洽，尽早 fail）。"""

    columns: list[str] = Field(min_length=1)
    rows: list[list[str]] = Field(default_factory=list)
    # 表格级引用：渲染层纳入全局 [n] 编号序列。
    citation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rows_consistent_with_columns(self) -> "ReportTable":
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(
                    f"ReportTable row length {len(row)} != columns length {len(self.columns)}"
                )
        return self


class ReportSection(BaseModel):
    heading: str
    claims: list[ResearchClaim] = Field(default_factory=list)  # 可选：兼容仅子节/仅表格
    subsections: list["ReportSection"] = Field(default_factory=list)  # 递归多级标题
    table: ReportTable | None = None  # 可选对比表格


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


# 解析递归自引用（subsections: list["ReportSection"]）。
ReportSection.model_rebuild()
