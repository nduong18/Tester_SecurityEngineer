from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


class TestCase(BaseModel):
    id: str = Field(description="Mã test case, ví dụ TC-001")
    title: str
    user_story: str = Field(default="", description="User story / acceptance criterion được kiểm thử")
    type: Literal["functional", "negative", "boundary", "security", "performance"] = "functional"
    priority: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_result: str = ""
    automatable: bool = True


class CreateTestCasesOutput(BaseModel):
    """Schema đầu ra cho action 'create_test_cases'."""
    summary: str
    test_cases: list[TestCase] = Field(default_factory=list)


class Finding(BaseModel):
    id: str
    severity: Severity
    title: str
    file: str = ""
    line: int = 0
    description: str = ""
    recommendation: str = ""
    source: str = ""


class RunTestSuiteOutput(BaseModel):
    """Schema đầu ra cho action 'run_test_suite'. PM đọc `verdict` + `findings` để quyết định Fix Loop."""
    verdict: Literal["PASS", "FAIL"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)
