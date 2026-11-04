import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar, Union
from pathlib import Path

from pydantic import BaseModel

from agent_contract.enums import AgentRole, AgentStatus, ArtifactType, ErrorCode, Priority, TaskStatus
from agent_contract.models import (
    AgentError,
    AgentIdentity,
    AgentState,
    ArtifactRef,
    NextAction,
    TaskRequest,
    TaskResult,
    blocked_result,
    failed_result,
    make_error,
)
from agent_contract.registry import ACTION_MAP, CAPABILITY_REGISTRY
from config import AgentConfig
from llm import LLMClient, LLMError
from prompt import TESTER_SYSTEM_PROMPT, build_messages
from schemas import CreateTestCasesOutput, Finding, RunTestSuiteOutput, TestCase
from tools.artifact import ArtifactError, save_artifact
from tools.workspace import find_input_files, iter_source_files, prepare_workspace

T = TypeVar("T", bound=BaseModel)


def action(
    name: str,
    output_model: type[BaseModel] | None = None,
    capability: str | None = None,
) -> Callable:
    """Decorator đăng ký action cho Agent."""
    def decorator(func: Callable) -> Callable:
        func._action_name = name  # type: ignore[attr-defined]
        func._output_model = output_model  # type: ignore[attr-defined]
        func._capability = capability  # type: ignore[attr-defined]
        return func
    return decorator


class BaseAgent:
    """Lớp nền tảng quản lý vòng đời tác vụ và bảo vệ lỗi chuẩn Contract v1.0."""

    def __init__(
        self,
        identity: AgentIdentity,
        config: AgentConfig,
        llm: LLMClient | None = None,
    ) -> None:
        self.identity = identity
        self.config = config
        self.llm = llm or LLMClient(config=config, mock=True)
        self._current_task: str | None = None
        self._actions: dict[str, tuple[Callable, type[BaseModel] | None, str | None]] = {}
        self._scan_and_register_actions()

    def _scan_and_register_actions(self) -> None:
        for attr_name in dir(self):
            try:
                attr = getattr(self, attr_name)
                if callable(attr) and hasattr(attr, "_action_name"):
                    action_name = getattr(attr, "_action_name")
                    output_model = getattr(attr, "_output_model", None)
                    capability = getattr(attr, "_capability", None)
                    self._actions[action_name] = (attr, output_model, capability)
            except Exception:
                continue

    def get_identity(self) -> AgentIdentity:
        return self.identity

    def get_state(self) -> AgentState:
        return AgentState(
            agent_id=self.identity.agent_id,
            role=self.identity.role,
            status=self.identity.status,
            current_task=self._current_task,
            updated_at=datetime.now(timezone.utc),
        )

    def handle(self, request: TaskRequest) -> TaskResult:
        start_time = time.perf_counter()

        if not request.instruction or not request.instruction.strip() or request.context.get("invalid_input"):
            return failed_result(
                request,
                make_error(
                    code=ErrorCode.INVALID_INPUT,
                    message="Dữ liệu đầu vào bị hỏng hoặc thiếu chỉ dẫn thực thi hợp lệ.",
                    retryable=False,
                ),
                summary="Dữ liệu đầu vào không hợp lệ.",
            )

        if request.role != self.identity.role:
            return failed_result(
                request,
                make_error(
                    code=ErrorCode.PERMISSION_DENIED,
                    message=f"Role mismatch: Agent role is {self.identity.role.value}, but request role is {request.role.value}.",
                    retryable=False,
                ),
            )

        if request.agent_id != self.identity.agent_id:
            return failed_result(
                request,
                make_error(
                    code=ErrorCode.PERMISSION_DENIED,
                    message=f"Agent ID mismatch: Agent is {self.identity.agent_id}, but request is for {request.agent_id}.",
                    retryable=False,
                ),
            )

        if request.action not in self._actions:
            return failed_result(
                request,
                make_error(
                    code=ErrorCode.PERMISSION_DENIED,
                    message=f"Action '{request.action}' không được hỗ trợ bởi agent {self.identity.agent_id}.",
                    retryable=False,
                ),
            )

        self.identity.status = AgentStatus.RUNNING
        self._current_task = request.task_id

        final_result: TaskResult

        try:
            handler, _, _ = self._actions[request.action]
            raw_output = handler(request)

            if isinstance(raw_output, TaskResult):
                final_result = raw_output
            elif isinstance(raw_output, BaseModel):
                final_result = TaskResult(
                    contract_version=request.contract_version,
                    task_id=request.task_id,
                    agent_id=request.agent_id,
                    status=TaskStatus.COMPLETED,
                    summary=f"Thực thi thành công action '{request.action}'",
                    outputs=[raw_output.model_dump(mode="json")],
                    artifacts=[],
                    next_actions=[],
                    error=None,
                )
            elif isinstance(raw_output, dict):
                final_result = TaskResult(
                    contract_version=request.contract_version,
                    task_id=request.task_id,
                    agent_id=request.agent_id,
                    status=TaskStatus.COMPLETED,
                    summary=f"Thực thi thành công action '{request.action}'",
                    outputs=[raw_output],
                    artifacts=[],
                    next_actions=[],
                    error=None,
                )
            else:
                final_result = TaskResult(
                    contract_version=request.contract_version,
                    task_id=request.task_id,
                    agent_id=request.agent_id,
                    status=TaskStatus.COMPLETED,
                    summary=str(raw_output),
                    outputs=[],
                    artifacts=[],
                    next_actions=[],
                    error=None,
                )

            self.identity.status = AgentStatus.IDLE
            self._current_task = None

        except LLMError as err:
            self.identity.status = AgentStatus.FAILED
            self._current_task = None
            final_result = failed_result(
                request,
                make_error(code=ErrorCode.LLM_ERROR, message=str(err), retryable=True),
                summary=f"Lỗi LLM khi thực thi action '{request.action}'",
            )
        except ArtifactError as err:
            self.identity.status = AgentStatus.FAILED
            self._current_task = None
            final_result = failed_result(
                request,
                make_error(code=ErrorCode.TOOL_ERROR, message=str(err), retryable=True),
                summary=f"Lỗi artifact khi thực thi action '{request.action}'",
            )
        except ValueError as err:
            self.identity.status = AgentStatus.FAILED
            self._current_task = None
            final_result = failed_result(
                request,
                make_error(code=ErrorCode.INVALID_INPUT, message=str(err), retryable=False),
                summary=f"Dữ liệu đầu vào không hợp lệ khi thực thi action '{request.action}'",
            )
        except Exception as err:
            self.identity.status = AgentStatus.FAILED
            self._current_task = None
            final_result = failed_result(
                request,
                make_error(code=ErrorCode.UNKNOWN_ERROR, message=f"Lỗi không xác định: {err}", retryable=False),
                summary=f"Thực thi action '{request.action}' thất bại",
            )

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        final_result.metadata.setdefault("provider", self.config.llm_provider)
        final_result.metadata.setdefault("model", self.config.llm_model)
        final_result.metadata.setdefault("duration_ms", duration_ms)
        final_result.metadata.setdefault("dry_run", self.config.dry_run)

        self.identity.status = AgentStatus.IDLE
        return final_result


class TesterAgent(BaseAgent):
    """Sinh test case từ user story, chạy automated test suite trên SOURCE_CODE và xuất verdict PASS/FAIL."""

    __test__ = False  # tên bắt đầu bằng "Test": không để pytest thu thập nhầm

    def __init__(
        self,
        config: AgentConfig | None = None,
        llm: LLMClient | None = None,
        artifact_dir: Union[Path, str, None] = None,
    ) -> None:
        cfg = config or AgentConfig()
        if artifact_dir:
            cfg.artifact_dir = str(artifact_dir)
        identity = AgentIdentity(
            agent_id=cfg.agent_id or "tester-01",
            role=AgentRole.TESTER,
            name="Tester Agent",
            version="1.0.0",
            capabilities=CAPABILITY_REGISTRY[AgentRole.TESTER],
            status=AgentStatus.IDLE,
        )
        super().__init__(identity=identity, config=cfg, llm=llm)

    # ------------------------------------------------------------------ create_test_cases
    @action("create_test_cases", output_model=CreateTestCasesOutput, capability="test_case_generation")
    def handle_create_test_cases(self, request: TaskRequest) -> TaskResult:
        story_files = find_input_files(request, self.config.artifact_dir, {ArtifactType.USER_STORY, ArtifactType.REQUIREMENT})
        stories_text = "\n\n".join(f"### {p.name}\n{p.read_text(encoding='utf-8', errors='replace')}" for p in story_files)

        if not self.llm.mock or self.llm.mock_responder:
            messages = build_messages(
                TESTER_SYSTEM_PROMPT, request, CreateTestCasesOutput.model_json_schema(), inputs_text=stories_text[:20000]
            )
            data = self.llm.complete_json(messages, CreateTestCasesOutput)
        else:
            data = _offline_test_cases(stories_text or request.instruction)

        art = save_artifact(
            name="test-cases.json",
            type=ArtifactType.TEST_CASE,
            content=data.model_dump_json(indent=2),
            created_by=self.identity.agent_id,
            artifact_dir=self.config.artifact_dir,
        )
        return TaskResult(
            contract_version=request.contract_version,
            task_id=request.task_id,
            agent_id=request.agent_id,
            status=TaskStatus.COMPLETED,
            summary=f"Đã sinh {len(data.test_cases)} test case.",
            outputs=[data.model_dump(mode="json")],
            artifacts=[art],
            next_actions=[
                NextAction(suggested_role=AgentRole.DEV, action="implement_project",
                           reason="Test case đã sẵn sàng, DEV hiện thực và kèm unit test theo test case."),
            ],
        )

    # ------------------------------------------------------------------ run_test_suite
    @action("run_test_suite", output_model=RunTestSuiteOutput, capability="automated_testing")
    def handle_run_test_suite(self, request: TaskRequest) -> TaskResult:
        sources = find_input_files(request, self.config.artifact_dir, {ArtifactType.SOURCE_CODE})
        if not sources:
            return blocked_result(
                request,
                make_error(ErrorCode.MISSING_CONTEXT, "Thiếu input artifact SOURCE_CODE để chạy test.", retryable=False),
                summary="Không có SOURCE_CODE để kiểm thử.",
            )
        root = prepare_workspace(sources, self.config.workspace_dir, request.task_id)
        timeout = max(10, (request.constraints.timeout_seconds if request.constraints else 120) - 15)

        findings: list[Finding] = []
        stats = {"files": 0, "syntax_errors": 0, "tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}

        # 1. Kiểm tra cú pháp toàn bộ file Python
        py_files = list(iter_source_files(root, {".py"}))
        for py in py_files:
            stats["files"] += 1
            rel = py.relative_to(root).as_posix()
            try:
                compile(py.read_text(encoding="utf-8", errors="replace"), rel, "exec")
            except SyntaxError as e:
                stats["syntax_errors"] += 1
                findings.append(Finding(
                    id=f"SYNTAX-{stats['syntax_errors']:03d}", severity="HIGH", title="Lỗi cú pháp Python",
                    file=rel, line=e.lineno or 0, description=str(e.msg),
                    recommendation="Sửa lỗi cú pháp để module có thể import được.", source="py_compile",
                ))

        # 2. Chạy pytest nếu mã nguồn có test
        has_tests = any(p.name.startswith("test_") or p.name.endswith("_test.py") for p in py_files)
        log_tail = ""
        if not has_tests:
            findings.append(Finding(
                id="NO-TESTS", severity="MEDIUM", title="Không tìm thấy automated test",
                description="SOURCE_CODE không có file test_*.py / *_test.py.",
                recommendation="DEV bổ sung unit test bám theo test-cases.json.", source="tester",
            ))
        elif "pytest" not in self.config.get_allowed_commands_list():
            findings.append(Finding(
                id="PYTEST-DISABLED", severity="MEDIUM", title="pytest không nằm trong ALLOWED_COMMANDS",
                description="Bỏ qua bước chạy test.", recommendation="Thêm 'pytest' vào ALLOWED_COMMANDS.", source="tester",
            ))
        else:
            log_tail = _run_pytest(root, timeout, stats, findings)

        verdict = "FAIL" if any(f.severity in ("HIGH", "CRITICAL") for f in findings) else "PASS"
        data = RunTestSuiteOutput(
            verdict=verdict,
            summary=(f"{verdict}: {stats['passed']}/{stats['tests']} test pass, "
                     f"{stats['failed'] + stats['errors']} lỗi, {stats['syntax_errors']} lỗi cú pháp."),
            findings=findings,
            stats=stats,
        )
        art = save_artifact(
            name="test-report.md",
            type=ArtifactType.TEST_REPORT,
            content=_render_report(request, data, log_tail),
            created_by=self.identity.agent_id,
            artifact_dir=self.config.artifact_dir,
        )
        next_actions = (
            [NextAction(suggested_role=AgentRole.DEV, action="fix_bugs", reason="Test FAIL, cần sửa các finding HIGH.")]
            if verdict == "FAIL"
            else [NextAction(suggested_role=AgentRole.SECURITY, action="security_review", reason="Test PASS, chuyển rà soát bảo mật.")]
        )
        return TaskResult(
            contract_version=request.contract_version,
            task_id=request.task_id,
            agent_id=request.agent_id,
            status=TaskStatus.COMPLETED,
            summary=data.summary,
            outputs=[data.model_dump(mode="json")],
            artifacts=[art],
            next_actions=next_actions,
        )


def _offline_test_cases(text: str) -> CreateTestCasesOutput:
    """Chế độ không có LLM: mỗi dòng gạch đầu dòng / Given-When-Then thành 1 test case functional."""
    criteria = []
    for line in text.splitlines():
        raw = line.strip()
        s = raw.lstrip("-*•").strip()
        if s and (raw[:1] in "-*•" or s.lower().startswith(("given", "when", "khi", "ac", "acceptance"))):
            criteria.append(s)
    criteria = criteria or [text.strip()[:200] or "Chức năng chính hoạt động đúng"]
    cases = [
        TestCase(
            id=f"TC-{i:03d}", title=f"Kiểm tra: {c[:80]}", user_story=c,
            steps=["Chuẩn bị dữ liệu hợp lệ", "Thực hiện thao tác theo tiêu chí", "Quan sát kết quả"],
            expected_result=c,
        )
        for i, c in enumerate(criteria[:50], 1)
    ]
    cases.append(TestCase(
        id=f"TC-{len(cases) + 1:03d}", title="Dữ liệu đầu vào không hợp lệ bị từ chối", type="negative",
        steps=["Gửi dữ liệu rỗng / sai định dạng"], expected_result="Hệ thống trả lỗi validation, không crash",
    ))
    return CreateTestCasesOutput(summary=f"Sinh {len(cases)} test case (offline, không dùng LLM).", test_cases=cases)


def _run_pytest(root: Path, timeout: int, stats: dict[str, int], findings: list[Finding]) -> str:
    """Chạy pytest trong workspace, parse JUnit XML thành findings. Trả về đuôi log."""
    junit = root / ".tester-junit.xml"
    # Không truyền biến môi trường của agent (API key, token) cho code chưa tin cậy.
    # shortcut: chỉ cô lập env + thư mục, chưa sandbox thật; muốn an toàn hơn thì chạy trong Docker.
    keep = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "WINDIR"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)})
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", f"--junitxml={junit}"]
    try:
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        findings.append(Finding(id="TIMEOUT", severity="HIGH", title=f"Test suite chạy quá {timeout}s",
                                recommendation="Kiểm tra vòng lặp vô hạn / test treo.", source="pytest"))
        return ""
    log_tail = (proc.stdout + proc.stderr)[-4000:]

    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            stats["tests"] += 1
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            bad = case.find("failure")
            if bad is None:
                bad = case.find("error")
            if case.find("skipped") is not None:
                stats["skipped"] += 1
            elif bad is not None:
                stats["failed" if bad.tag == "failure" else "errors"] += 1
                findings.append(Finding(
                    id=f"TEST-{stats['failed'] + stats['errors']:03d}", severity="HIGH",
                    title=f"Test {'FAILED' if bad.tag == 'failure' else 'ERROR'}: {name}",
                    file=case.get("file", ""), line=int(case.get("line") or 0),
                    description=((bad.get("message") or "")[:500] + "\n" + (bad.text or "")[-1500:]).strip(),
                    recommendation="Sửa mã nguồn để test pass.", source="pytest",
                ))
            else:
                stats["passed"] += 1
    # exit 0 = pass, 1 = có test fail (đã ghi ở trên), 5 = không collect được test; còn lại là pytest crash
    if proc.returncode not in (0, 1, 5) or (proc.returncode == 1 and stats["failed"] + stats["errors"] == 0):
        findings.append(Finding(id="PYTEST-CRASH", severity="HIGH", title=f"pytest lỗi (exit {proc.returncode})",
                                description=log_tail[-1500:], recommendation="Sửa lỗi import / cấu hình test.", source="pytest"))
    return log_tail


def _render_report(request: TaskRequest, data: RunTestSuiteOutput, log_tail: str) -> str:
    lines = [
        "# Test Report", "",
        f"- **Task:** {request.task_id}",
        f"- **Project:** {request.project_id}",
        f"- **Verdict:** **{data.verdict}**",
        f"- **Tóm tắt:** {data.summary}", "",
        "## Thống kê", "", "| Chỉ số | Giá trị |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in data.stats.items()], "",
        "## Findings", "",
    ]
    if not data.findings:
        lines.append("Không có finding.")
    for f in data.findings:
        loc = f" — `{f.file}:{f.line}`" if f.file else ""
        lines += [f"### [{f.severity}] {f.id}: {f.title}{loc}", "", f.description, "", f"**Khắc phục:** {f.recommendation}", ""]
    if log_tail:
        lines += ["## Log pytest (cuối)", "", "```", log_tail, "```"]
    return "\n".join(lines) + "\n"
