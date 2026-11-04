import json
from datetime import datetime

from agent_contract.enums import AgentRole, ArtifactType, ErrorCode, Priority, TaskStatus
from agent_contract.models import TaskRequest, TaskResult


def test_example_1_task_request():
    """Kiểm tra ví dụ TaskRequest ở mục 2.4 deserialize và validate thành công."""
    raw_json = """
    {
      "contract_version": "1.0.0",
      "task_id": "TASK-001",
      "project_id": "PROJECT-001",
      "agent_id": "pm-01",
      "role": "PM",
      "action": "create_plan",
      "instruction": "Lập kế hoạch 6 sprint cho web bán hàng, 5 người, 3 tháng",
      "context": {"team_size": 5, "duration_weeks": 12},
      "input_artifacts": [],
      "constraints": {"deadline": null, "priority": "HIGH", "timeout_seconds": 300}
    }
    """
    req = TaskRequest.model_validate_json(raw_json)
    assert req.contract_version == "1.0.0"
    assert req.task_id == "TASK-001"
    assert req.project_id == "PROJECT-001"
    assert req.agent_id == "pm-01"
    assert req.role == AgentRole.PM
    assert req.action == "create_plan"
    assert req.instruction == "Lập kế hoạch 6 sprint cho web bán hàng, 5 người, 3 tháng"
    assert req.context["team_size"] == 5
    assert req.context["duration_weeks"] == 12
    assert req.input_artifacts == []
    assert req.constraints.priority == Priority.HIGH
    assert req.constraints.timeout_seconds == 300
    assert req.constraints.deadline is None


def test_example_2_task_result_success():
    """Kiểm tra ví dụ TaskResult thành công ở mục 2.4 deserialize và validate thành công."""
    raw_json = """
    {
      "contract_version": "1.0.0",
      "task_id": "TASK-001",
      "agent_id": "pm-01",
      "status": "COMPLETED",
      "summary": "Đã tạo kế hoạch 6 sprint gồm 4 milestone",
      "outputs": [{"milestones": 4, "sprints": 6}],
      "artifacts": [{"artifact_id": "ART-001", "name": "plan.md", "type": "PLAN", "version": "1.0", "uri": "artifacts/plan.md", "created_by": "pm-01", "created_at": "2026-11-02T09:00:00Z", "checksum": null}],
      "next_actions": [{"suggested_role": "BA", "action": "analyze_requirement", "reason": "Cần chi tiết hóa yêu cầu cho sprint 1"}],
      "error": null,
      "metadata": {"provider": "groq", "model": "llama-3.3-70b-versatile", "duration_ms": 4200}
    }
    """
    res = TaskResult.model_validate_json(raw_json)
    assert res.contract_version == "1.0.0"
    assert res.task_id == "TASK-001"
    assert res.agent_id == "pm-01"
    assert res.status == TaskStatus.COMPLETED
    assert res.summary == "Đã tạo kế hoạch 6 sprint gồm 4 milestone"
    assert res.outputs == [{"milestones": 4, "sprints": 6}]
    assert len(res.artifacts) == 1
    art = res.artifacts[0]
    assert art.artifact_id == "ART-001"
    assert art.name == "plan.md"
    assert art.type == ArtifactType.PLAN
    assert art.uri == "artifacts/plan.md"
    assert art.created_by == "pm-01"
    assert art.created_at == datetime.fromisoformat("2026-11-02T09:00:00Z")
    assert art.checksum is None
    assert len(res.next_actions) == 1
    assert res.next_actions[0].suggested_role == AgentRole.BA
    assert res.next_actions[0].action == "analyze_requirement"
    assert res.error is None
    assert res.metadata["provider"] == "groq"
    assert res.metadata["model"] == "llama-3.3-70b-versatile"
    assert res.metadata["duration_ms"] == 4200


def test_example_3_task_result_failed():
    """Kiểm tra ví dụ TaskResult lỗi ở mục 2.4 deserialize và validate thành công."""
    raw_json = """
    {
      "contract_version": "1.0.0",
      "task_id": "TASK-002",
      "agent_id": "devops-01",
      "status": "FAILED",
      "summary": "Build Docker image thất bại",
      "outputs": [], "artifacts": [], "next_actions": [],
      "error": {"code": "TOOL_ERROR", "message": "docker build exit code 1", "retryable": true, "details": {"stderr_tail": "..."}},
      "metadata": {}
    }
    """
    res = TaskResult.model_validate_json(raw_json)
    assert res.contract_version == "1.0.0"
    assert res.task_id == "TASK-002"
    assert res.agent_id == "devops-01"
    assert res.status == TaskStatus.FAILED
    assert res.summary == "Build Docker image thất bại"
    assert res.outputs == []
    assert res.artifacts == []
    assert res.next_actions == []
    assert res.error is not None
    assert res.error.code == ErrorCode.TOOL_ERROR
    assert res.error.message == "docker build exit code 1"
    assert res.error.retryable is True
    assert res.error.details == {"stderr_tail": "..."}
