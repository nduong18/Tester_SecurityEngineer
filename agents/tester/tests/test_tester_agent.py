import io
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

agent_dir = Path(__file__).resolve().parent.parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

for key in list(sys.modules.keys()):
    if key in ["agent", "config", "llm", "prompt", "schemas", "api", "tools"] or key.startswith("tools."):
        sys.modules.pop(key, None)

import pytest
from agent_contract.enums import AgentRole, ArtifactType, ErrorCode, TaskStatus
from agent_contract.models import ArtifactRef, TaskRequest
from agent import TesterAgent
from config import AgentConfig
from llm import LLMClient


@pytest.fixture
def agent(tmp_path) -> TesterAgent:
    cfg = AgentConfig(
        base_dir=tmp_path, port=8007, agent_id="tester-01", agent_role=AgentRole.TESTER,
        artifact_dir=str(tmp_path / "artifacts"), workspace_dir=str(tmp_path / "workspace"),
        data_dir=str(tmp_path / "data"), allowed_commands="python,pytest",
    )
    return TesterAgent(config=cfg, llm=LLMClient(config=cfg, mock=True))


def relay(agent: TesterAgent, name: str, content: bytes, type_: ArtifactType, origin: str = "dev-01") -> ArtifactRef:
    """Giả lập PM relay: file nằm ở artifacts/inbox/<origin>/<name>, ArtifactRef giữ uri gốc."""
    inbox = Path(agent.config.artifact_dir) / "inbox" / origin
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_bytes(content)
    return ArtifactRef(artifact_id="ART-001", name=name, type=type_, uri=f"artifacts/{name}",
                       created_by=origin, created_at=datetime.now(timezone.utc))


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, c in files.items():
            zf.writestr(n, c)
    return buf.getvalue()


def req(action: str, inputs=(), instruction="Chạy kiểm thử") -> TaskRequest:
    return TaskRequest(task_id=f"TASK-T-{action}", project_id="PROJ-TEST", agent_id="tester-01",
                       role=AgentRole.TESTER, action=action, instruction=instruction, input_artifacts=list(inputs))


def test_identity(agent):
    assert agent.get_identity().agent_id == "tester-01"
    assert set(agent._actions) == {"create_test_cases", "run_test_suite"}


def test_invalid_action(agent):
    res = agent.handle(req("hack"))
    assert res.status == TaskStatus.FAILED and res.error.code == ErrorCode.PERMISSION_DENIED


def test_create_test_cases_from_user_story(agent):
    story = b"# US-01 Login\n- Given valid credentials, user is logged in\n- Wrong password shows an error\n"
    ref = relay(agent, "user-stories.md", story, ArtifactType.USER_STORY, origin="ba-01")
    res = agent.handle(req("create_test_cases", [ref]))
    assert res.status == TaskStatus.COMPLETED, res.error
    assert res.artifacts[0].type == ArtifactType.TEST_CASE
    assert len(res.outputs[0]["test_cases"]) == 3  # 2 tiêu chí + 1 negative


def test_run_suite_pass(agent):
    src = make_zip({"calc.py": "def add(a, b):\n    return a + b\n",
                    "tests/test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"})
    res = agent.handle(req("run_test_suite", [relay(agent, "source.zip", src, ArtifactType.SOURCE_CODE)]))
    out = res.outputs[0]
    assert out["verdict"] == "PASS", out
    assert out["stats"]["passed"] == 1
    assert res.artifacts[0].type == ArtifactType.TEST_REPORT


def test_run_suite_fail_gives_high_findings(agent):
    src = make_zip({"calc.py": "def add(a, b):\n    return a - b\n",
                    "broken.py": "def x(:\n",
                    "test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"})
    res = agent.handle(req("run_test_suite", [relay(agent, "source.zip", src, ArtifactType.SOURCE_CODE)]))
    out = res.outputs[0]
    assert out["verdict"] == "FAIL"
    ids = {f["id"] for f in out["findings"]}
    assert "SYNTAX-001" in ids and "TEST-001" in ids
    assert res.next_actions[0].action == "fix_bugs"


def test_run_suite_without_source_is_blocked(agent):
    res = agent.handle(req("run_test_suite"))
    assert res.status == TaskStatus.BLOCKED and res.error.code == ErrorCode.MISSING_CONTEXT


def test_zip_slip_rejected(agent):
    src = make_zip({"../../evil.py": "x = 1\n"})
    res = agent.handle(req("run_test_suite", [relay(agent, "source.zip", src, ArtifactType.SOURCE_CODE)]))
    assert res.status == TaskStatus.FAILED and res.error.code == ErrorCode.TOOL_ERROR
    assert not (Path(agent.config.workspace_dir).parent / "evil.py").exists()
