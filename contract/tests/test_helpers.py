from agent_contract.enums import AgentRole, ErrorCode, TaskStatus
from agent_contract.models import (
    TaskRequest,
    blocked_result,
    failed_result,
    make_error,
)


def test_make_error():
    """Kiểm tra hàm tiện ích make_error tạo đúng cấu trúc AgentError."""
    err = make_error(
        code=ErrorCode.PERMISSION_DENIED,
        message="Không có quyền chạy lệnh deploy prod",
        retryable=False,
        command="deploy prod",
        operator="admin",
    )
    assert err.code == ErrorCode.PERMISSION_DENIED
    assert err.message == "Không có quyền chạy lệnh deploy prod"
    assert err.retryable is False
    assert err.details == {"command": "deploy prod", "operator": "admin"}


def test_failed_result():
    """Kiểm tra hàm tiện ích failed_result tạo đúng TaskResult có status FAILED."""
    req = TaskRequest(
        task_id="TASK-100",
        project_id="PROJ-001",
        agent_id="devops-01",
        role=AgentRole.DEVOPS,
        action="build_image",
        instruction="Build image frontend",
    )
    err = make_error(
        code=ErrorCode.TOOL_ERROR,
        message="docker build exit 1",
        retryable=True,
    )
    result = failed_result(req, err)

    assert result.contract_version == req.contract_version
    assert result.task_id == req.task_id
    assert result.agent_id == req.agent_id
    assert result.status == TaskStatus.FAILED
    assert result.summary == "docker build exit 1"
    assert result.error == err
    assert result.outputs == []
    assert result.artifacts == []
    assert result.next_actions == []


def test_blocked_result():
    """Kiểm tra hàm tiện ích blocked_result tạo đúng TaskResult có status BLOCKED."""
    req = TaskRequest(
        task_id="TASK-200",
        project_id="PROJ-001",
        agent_id="pm-01",
        role=AgentRole.PM,
        action="create_plan",
        instruction="Lập kế hoạch sprint",
    )
    err = make_error(
        code=ErrorCode.MISSING_CONTEXT,
        message="Cần làm rõ deadline và ngân sách dự án",
        retryable=False,
        questions=["Deadline là ngày nào?", "Ngân sách bao nhiêu?"],
    )
    result = blocked_result(req, err, summary="Task bị chặn do thiếu context")

    assert result.contract_version == req.contract_version
    assert result.task_id == req.task_id
    assert result.agent_id == req.agent_id
    assert result.status == TaskStatus.BLOCKED
    assert result.summary == "Task bị chặn do thiếu context"
    assert result.error == err
    assert result.error.details["questions"] == ["Deadline là ngày nào?", "Ngân sách bao nhiêu?"]
