from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


class Finding(BaseModel):
    id: str
    severity: Severity
    title: str
    file: str = ""
    line: int = 0
    description: str = ""
    recommendation: str = ""
    source: str = ""


class ArchitectureReview(BaseModel):
    """Đánh giá threat model từ LLM trên tài liệu ARCHITECTURE (chỉ tham khảo, không ảnh hưởng verdict)."""
    summary: str
    recommendations: list[str] = Field(default_factory=list)


class SecurityReviewOutput(BaseModel):
    """Schema đầu ra cho action 'security_review'. PM chỉ mở Fix Loop khi có finding HIGH/CRITICAL."""
    verdict: Literal["PASS", "FAIL"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)
    architecture_review: ArchitectureReview | None = None
