from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from agent_contract.enums import AgentRole, AgentStatus, ArtifactType, ErrorCode, TaskStatus
from agent_contract.models import (
    AgentError,
    AgentIdentity,
    ArtifactRef,
    TaskRequest,
    TaskResult,
)


class TestTaskResultStatusAndError:
    """Kiểm tra ràng buộc giữa status và error trong TaskResult."""

    def test_completed_with_none_error_is_valid(self):
        res = TaskResult(
            task_id="TASK-001",
            agent_id="pm-01",
            status=TaskStatus.COMPLETED,
            summary="Thành công",
            error=None,
        )
        assert res.status == TaskStatus.COMPLETED
        assert res.error is None

    def test_completed_with_error_raises_validation_error(self):
        err = AgentError(code=ErrorCode.UNKNOWN_ERROR, message="Lỗi lạ", retryable=False)
        with pytest.raises(ValidationError, match="status là COMPLETED, trường 'error' bắt buộc phải là None"):
            TaskResult(
                task_id="TASK-001",
                agent_id="pm-01",
                status=TaskStatus.COMPLETED,
                summary="Không được phép có lỗi",
                error=err,
            )

    def test_failed_with_error_is_valid(self):
        err = AgentError(code=ErrorCode.TOOL_ERROR, message="Lỗi tool", retryable=True)
        res = TaskResult(
            task_id="TASK-002",
            agent_id="devops-01",
            status=TaskStatus.FAILED,
            summary="Thất bại",
            error=err,
        )
        assert res.status == TaskStatus.FAILED
        assert res.error == err

    def test_failed_without_error_raises_validation_error(self):
        with pytest.raises(ValidationError, match="status là FAILED, trường 'error' bắt buộc phải có thông tin"):
            TaskResult(
                task_id="TASK-002",
                agent_id="devops-01",
                status=TaskStatus.FAILED,
                summary="Thất bại mà thiếu error",
                error=None,
            )

    def test_blocked_with_error_is_valid(self):
        err = AgentError(code=ErrorCode.MISSING_CONTEXT, message="Thiếu context", retryable=False)
        res = TaskResult(
            task_id="TASK-003",
            agent_id="pm-01",
            status=TaskStatus.BLOCKED,
            summary="Bị chặn",
            error=err,
        )
        assert res.status == TaskStatus.BLOCKED
        assert res.error == err

    def test_blocked_without_error_raises_validation_error(self):
        with pytest.raises(ValidationError, match="status là BLOCKED, trường 'error' bắt buộc phải có thông tin"):
            TaskResult(
                task_id="TASK-003",
                agent_id="pm-01",
                status=TaskStatus.BLOCKED,
                summary="Bị chặn nhưng thiếu error",
                error=None,
            )

    def test_cancelled_status_allows_both_none_and_error(self):
        # CANCELLED với error=None
        res1 = TaskResult(
            task_id="TASK-004",
            agent_id="pm-01",
            status=TaskStatus.CANCELLED,
            summary="Đã hủy bỏ bởi người dùng",
            error=None,
        )
        assert res1.status == TaskStatus.CANCELLED
        assert res1.error is None

        # CANCELLED với error có giá trị
        err = AgentError(code=ErrorCode.TIMEOUT, message="Hết giờ thực thi", retryable=False)
        res2 = TaskResult(
            task_id="TASK-005",
            agent_id="pm-01",
            status=TaskStatus.CANCELLED,
            summary="Đã hủy do timeout",
            error=err,
        )
        assert res2.status == TaskStatus.CANCELLED
        assert res2.error == err


class TestAgentCapabilitiesValidation:
    """Kiểm tra ràng buộc capabilities phải là tập con của CAPABILITY_REGISTRY[role]."""

    def test_valid_capabilities_subset(self):
        identity = AgentIdentity(
            agent_id="pm-01",
            role=AgentRole.PM,
            name="PM Agent",
            version="1.0.0",
            capabilities=["project_planning", "task_decomposition"],
        )
        assert identity.capabilities == ["project_planning", "task_decomposition"]

    def test_invalid_capability_raises_validation_error(self):
        # "docker" thuộc DevOps, không thuộc PM
        with pytest.raises(ValidationError, match="chứa capability không hợp lệ"):
            AgentIdentity(
                agent_id="pm-01",
                role=AgentRole.PM,
                name="PM Agent",
                version="1.0.0",
                capabilities=["project_planning", "docker"],
            )

    def test_unknown_capability_raises_validation_error(self):
        with pytest.raises(ValidationError, match="chứa capability không hợp lệ"):
            AgentIdentity(
                agent_id="devops-01",
                role=AgentRole.DEVOPS,
                name="DevOps Agent",
                version="1.0.0",
                capabilities=["git", "hacking_the_moon"],
            )


class TestArtifactUriValidation:
    """Kiểm tra ràng buộc uri tương đối và không chứa '..'."""

    def test_valid_relative_uris(self):
        now = datetime.now(timezone.utc)
        valid_uris = [
            "artifacts/plan.md",
            "plan.md",
            "docs/reports/summary.json",
            "a/b/c/d.txt",
        ]
        for uri in valid_uris:
            art = ArtifactRef(
                artifact_id="ART-001",
                name="File",
                type=ArtifactType.PLAN,
                uri=uri,
                created_by="pm-01",
                created_at=now,
            )
            assert art.uri == uri

    def test_uri_with_path_traversal_raises_validation_error(self):
        now = datetime.now(timezone.utc)
        invalid_traversals = [
            "../secret.txt",
            "artifacts/../secret.txt",
            "a/b/../../secret.txt",
            "..\\windows\\secret.txt",
        ]
        for uri in invalid_traversals:
            with pytest.raises(ValidationError, match="không được chứa '..'"):
                ArtifactRef(
                    artifact_id="ART-001",
                    name="File",
                    type=ArtifactType.PLAN,
                    uri=uri,
                    created_by="pm-01",
                    created_at=now,
                )

    def test_absolute_uris_raise_validation_error(self):
        now = datetime.now(timezone.utc)
        absolute_uris = [
            "/etc/passwd",
            "/home/user/plan.md",
            "C:/artifacts/plan.md",
            "D:\\data\\plan.md",
        ]
        for uri in absolute_uris:
            with pytest.raises(ValidationError, match="phải là đường dẫn tương đối"):
                ArtifactRef(
                    artifact_id="ART-001",
                    name="File",
                    type=ArtifactType.PLAN,
                    uri=uri,
                    created_by="pm-01",
                    created_at=now,
                )

    def test_empty_uri_raises_validation_error(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError, match="không được để trống"):
            ArtifactRef(
                artifact_id="ART-001",
                name="File",
                type=ArtifactType.PLAN,
                uri="   ",
                created_by="pm-01",
                created_at=now,
            )


class TestAgentIdRegexValidation:
    """Kiểm tra ràng buộc agent_id theo định dạng regex ^[a-z_]+-\\d{2}$."""

    def test_valid_agent_ids(self):
        valid_ids = ["pm-01", "devops-02", "scrum_master-10", "qa_lead-99"]
        for aid in valid_ids:
            req = TaskRequest(
                task_id="TASK-001",
                project_id="PROJ-001",
                agent_id=aid,
                role=AgentRole.PM,
                action="create_plan",
                instruction="Làm gì đó",
            )
            assert req.agent_id == aid

    def test_invalid_agent_ids_raise_validation_error(self):
        invalid_ids = [
            "PM-01",       # viết hoa
            "pm-1",        # chỉ có 1 chữ số
            "pm-001",      # 3 chữ số
            "pm_01",       # dùng dấu gạch dưới thay vì gạch nối
            "pm",          # thiếu phần số
            "-01",         # thiếu tên role
            "pm-01-extra", # dư thừa
            "",            # rỗng
        ]
        for aid in invalid_ids:
            with pytest.raises(ValidationError, match="không hợp lệ"):
                TaskRequest(
                    task_id="TASK-001",
                    project_id="PROJ-001",
                    agent_id=aid,
                    role=AgentRole.PM,
                    action="create_plan",
                    instruction="Làm gì đó",
                )
