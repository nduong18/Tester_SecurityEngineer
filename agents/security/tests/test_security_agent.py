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
from agent import SecurityAgent
from config import AgentConfig
from llm import LLMClient

VULNERABLE = '''import os, pickle, subprocess, sqlite3
API_KEY = "sk-live-9f8e7d6c5b4a"

def run(cmd):
    subprocess.call(cmd, shell=True)

def find(conn, name):
    return conn.execute(f"SELECT * FROM users WHERE name = '{name}'")

def load(blob):
    return pickle.loads(blob)
'''

CLEAN = '''import os, sqlite3

API_KEY = os.environ.get("API_KEY", "")

def find(conn, name):
    return conn.execute("SELECT * FROM users WHERE name = ?", (name,))
'''


@pytest.fixture
def agent(tmp_path) -> SecurityAgent:
    cfg = AgentConfig(
        base_dir=tmp_path, port=8010, agent_id="security-01", agent_role=AgentRole.SECURITY,
        artifact_dir=str(tmp_path / "artifacts"), workspace_dir=str(tmp_path / "workspace"),
        data_dir=str(tmp_path / "data"), allowed_commands="python",  # tắt bandit/pip-audit để test ổn định
    )
    return SecurityAgent(config=cfg, llm=LLMClient(config=cfg, mock=True))


def review(agent: SecurityAgent, files: dict[str, str]):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, c in files.items():
            zf.writestr(n, c)
    inbox = Path(agent.config.artifact_dir) / "inbox" / "dev-01"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "source.zip").write_bytes(buf.getvalue())
    ref = ArtifactRef(artifact_id="ART-001", name="source.zip", type=ArtifactType.SOURCE_CODE,
                      uri="artifacts/source.zip", created_by="dev-01", created_at=datetime.now(timezone.utc))
    return agent.handle(TaskRequest(task_id="TASK-SEC-1", project_id="PROJ-TEST", agent_id="security-01",
                                    role=AgentRole.SECURITY, action="security_review",
                                    instruction="Rà soát bảo mật", input_artifacts=[ref]))


def test_identity(agent):
    assert agent.get_identity().role == AgentRole.SECURITY
    assert list(agent._actions) == ["security_review"]


def test_vulnerable_code_fails(agent):
    res = review(agent, {"app.py": VULNERABLE, ".env": "SECRET=abc\n"})
    assert res.status == TaskStatus.COMPLETED, res.error
    out = res.outputs[0]
    assert out["verdict"] == "FAIL"
    ids = {f["id"] for f in out["findings"]}
    assert {"SEC-SECRET", "SEC-SHELL", "SEC-SQLI", "SEC-PICKLE", "SEC-ENVFILE"} <= ids
    assert res.artifacts[0].type == ArtifactType.SECURITY_REPORT
    assert res.next_actions[0].action == "fix_bugs"


def test_clean_code_passes(agent):
    res = review(agent, {"app.py": CLEAN, ".env.example": "API_KEY=\n"})
    out = res.outputs[0]
    assert out["verdict"] == "PASS", out["findings"]


def test_missing_source_blocked(agent):
    res = agent.handle(TaskRequest(task_id="TASK-SEC-2", project_id="P", agent_id="security-01",
                                   role=AgentRole.SECURITY, action="security_review", instruction="x"))
    assert res.status == TaskStatus.BLOCKED and res.error.code == ErrorCode.MISSING_CONTEXT
