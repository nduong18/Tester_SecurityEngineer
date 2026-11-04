import json
import subprocess
import time
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
from prompt import SECURITY_SYSTEM_PROMPT, build_messages
from schemas import ArchitectureReview, Finding, SecurityReviewOutput
from tools.artifact import ArtifactError, save_artifact
from tools.scanner import has_tool, run_bandit, run_pip_audit, scan_rules
from tools.workspace import find_input_files, prepare_workspace

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


SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


class SecurityAgent(BaseAgent):
    """Rà soát bảo mật SOURCE_CODE (quét tĩnh + dependency) và ARCHITECTURE, xuất verdict PASS/FAIL."""

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
            agent_id=cfg.agent_id or "security-01",
            role=AgentRole.SECURITY,
            name="Security Agent",
            version="1.0.0",
            capabilities=CAPABILITY_REGISTRY[AgentRole.SECURITY],
            status=AgentStatus.IDLE,
        )
        super().__init__(identity=identity, config=cfg, llm=llm)

    @action("security_review", output_model=SecurityReviewOutput, capability="security_review")
    def handle_security_review(self, request: TaskRequest) -> TaskResult:
        sources = find_input_files(request, self.config.artifact_dir, {ArtifactType.SOURCE_CODE})
        arch_files = find_input_files(request, self.config.artifact_dir, {ArtifactType.ARCHITECTURE})
        if not sources:
            return blocked_result(
                request,
                make_error(ErrorCode.MISSING_CONTEXT, "Thiếu input artifact SOURCE_CODE để rà soát bảo mật.", retryable=False),
                summary="Không có SOURCE_CODE để rà soát.",
            )
        root = prepare_workspace(sources, self.config.workspace_dir, request.task_id)
        timeout = max(10, (request.constraints.timeout_seconds if request.constraints else 120) // 3)
        allowed = self.config.get_allowed_commands_list()

        findings, files_scanned = scan_rules(root)
        tools_used = ["rules"]
        for cmd, module, runner in (("bandit", "bandit", run_bandit), ("pip-audit", "pip_audit", run_pip_audit)):
            if cmd not in allowed or not has_tool(module):
                continue
            try:
                findings += runner(root, timeout)
                tools_used.append(cmd)
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
                findings.append(Finding(id=f"{cmd.upper()}-ERROR", severity="INFO", title=f"{cmd} không chạy được",
                                        description=str(e)[:300], source=cmd))

        # Gộp trùng (cùng rule + file + line) rồi sắp xếp theo mức độ
        unique = {(f.id, f.file, f.line): f for f in findings}
        findings = sorted(unique.values(), key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line))

        arch_review = None
        if arch_files and (not self.llm.mock or self.llm.mock_responder):
            arch_text = "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in arch_files)[:15000]
            top = "\n".join(f"- [{f.severity}] {f.title} ({f.file}:{f.line})" for f in findings[:30])
            messages = build_messages(
                SECURITY_SYSTEM_PROMPT, request, ArchitectureReview.model_json_schema(),
                inputs_text=f"## ARCHITECTURE\n{arch_text}\n\n## Kết quả quét tĩnh\n{top or 'Không có'}",
            )
            arch_review = self.llm.complete_json(messages, ArchitectureReview)

        stats = {sev: sum(f.severity == sev for f in findings) for sev in SEVERITY_ORDER}
        stats["files_scanned"] = files_scanned
        verdict = "FAIL" if stats["CRITICAL"] + stats["HIGH"] else "PASS"
        data = SecurityReviewOutput(
            verdict=verdict,
            summary=(f"{verdict}: {stats['CRITICAL']} CRITICAL, {stats['HIGH']} HIGH, {stats['MEDIUM']} MEDIUM, "
                     f"{stats['LOW']} LOW trên {files_scanned} file (công cụ: {', '.join(tools_used)})."),
            findings=findings,
            stats=stats,
            architecture_review=arch_review,
        )
        art = save_artifact(
            name="security-report.md",
            type=ArtifactType.SECURITY_REPORT,
            content=_render_report(request, data),
            created_by=self.identity.agent_id,
            artifact_dir=self.config.artifact_dir,
        )
        next_actions = (
            [NextAction(suggested_role=AgentRole.DEV, action="fix_bugs", reason="Có lỗ hổng HIGH/CRITICAL cần khắc phục.")]
            if verdict == "FAIL"
            else [NextAction(suggested_role=AgentRole.DEVOPS, action="generate_ci_config", reason="Không có lỗ hổng nghiêm trọng.")]
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


def _render_report(request: TaskRequest, data: SecurityReviewOutput) -> str:
    lines = [
        "# Security Report", "",
        f"- **Task:** {request.task_id}",
        f"- **Project:** {request.project_id}",
        f"- **Verdict:** **{data.verdict}**",
        f"- **Tóm tắt:** {data.summary}", "",
        "## Thống kê", "", "| Mức độ | Số lượng |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in data.stats.items()], "",
        "## Findings", "",
    ]
    if not data.findings:
        lines.append("Không phát hiện lỗ hổng.")
    else:
        lines += ["| Mức độ | Rule | Vị trí | Mô tả | Khắc phục |", "|---|---|---|---|---|"]
        for f in data.findings:
            desc = f"{f.title}: `{f.description}`" if f.description else f.title
            cells = [f.severity, f.id, f"`{f.file}:{f.line}`", desc, f.recommendation]
            lines.append("| " + " | ".join(c.replace("|", r"\|") for c in cells) + " |")
    if data.architecture_review:
        lines += ["", "## Đánh giá kiến trúc (LLM)", "", data.architecture_review.summary, ""]
        lines += [f"- {r}" for r in data.architecture_review.recommendations]
    return "\n".join(lines) + "\n"
