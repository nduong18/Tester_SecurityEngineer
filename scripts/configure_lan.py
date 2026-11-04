"""scripts/configure_lan.py - Tiện ích hỗ trợ cấu hình tự động kết nối LAN cho 10 AI Agent trên 5 máy.

Cung cấp các lệnh:
1. setup-machine <1..5>: Tự động sinh file .env và token bảo mật cho 2 agent trên máy hiện tại.
2. export-tokens: Trích xuất AGENT_API_KEY của các agent trên máy này để chia sẻ cho file .env.lan.
3. gen-endpoints: Sinh file scripts/endpoints.yaml từ danh sách địa chỉ IP của 5 máy.
"""

import argparse
import io
import os
import secrets
import sys
from pathlib import Path

def _setup_console_encoding() -> None:
    if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

_setup_console_encoding()

ROOT_DIR = Path(__file__).resolve().parent.parent

# Bảng phân bổ 10 Agent trên 5 Máy
MACHINE_ROLES = {
    1: [
        {"dir": "pm", "id": "pm-01", "role": "PM", "port": 8001},
        {"dir": "devops", "id": "devops-01", "role": "DEVOPS", "port": 8002},
    ],
    2: [
        {"dir": "ba", "id": "ba-01", "role": "BA", "port": 8003},
        {"dir": "sa", "id": "sa-01", "role": "SA", "port": 8004},
    ],
    3: [
        {"dir": "po", "id": "po-01", "role": "PO", "port": 8005},
        {"dir": "dev", "id": "dev-01", "role": "DEV", "port": 8006},
    ],
    4: [
        {"dir": "tester", "id": "tester-01", "role": "TESTER", "port": 8007},
        {"dir": "dba", "id": "dba-01", "role": "DBA", "port": 8008},
    ],
    5: [
        {"dir": "scrum_master", "id": "scrum_master-01", "role": "SCRUM_MASTER", "port": 8009},
        {"dir": "security", "id": "security-01", "role": "SECURITY", "port": 8010},
    ],
}


def generate_env_content(agent_info: dict, token: str) -> str:
    agent_dir = agent_info["dir"]
    role = agent_info["role"]
    port = agent_info["port"]
    agent_id = agent_info["id"]

    conductor_settings = ""
    if role == "PM":
        conductor_settings = """
# Cấu hình Conductor tự động hóa quy trình (v1.1.0)
PM_AUTO_APPROVE=false
PM_ALLOW_REAL_DEPLOY=false
MAX_FIX_LOOPS=3
MAX_TASKS_PER_PROJECT=60
PROJECT_TIMEOUT_MINUTES=240
"""

    return f"""# ==============================================================================
# CẤU HÌNH TỰ ĐỘNG CHO AGENT: {agent_id} (Role: {role})
# ==============================================================================

# 1. Mạng và Cổng lắng nghe (BẮT BUỘC cho LAN)
HOST=0.0.0.0
PORT={port}
CONTRACT_MIN_VERSION=1.0.0

# 2. Định danh Agent
AGENT_ID={agent_id}
AGENT_ROLE={role}

# 3. Khóa bảo mật Bearer Token (Xác thực HTTP Agent API v1)
AGENT_API_KEY={token}
MAX_UPLOAD_MB=50

# 4. Cấu hình LLM (Chọn provider: groq | gemini | openrouter | ollama | openai)
LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile
LLM_API_KEY=
LLM_BASE_URL=
LLM_TEMPERATURE=0.2
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2

# 5. Thư mục dữ liệu cô lập cục bộ
ARTIFACT_DIR=./artifacts
WORKSPACE_DIR=./workspace
DATA_DIR=./data
ALLOWED_COMMANDS=python,pytest
DRY_RUN=true
{conductor_settings}"""


def cmd_setup_machine(args: argparse.Namespace) -> None:
    machine_no = args.machine
    if machine_no not in MACHINE_ROLES:
        print(f"Lỗi: Số máy phải từ 1 đến 5 (nhận: {machine_no})", file=sys.stderr)
        sys.exit(1)

    agents = MACHINE_ROLES[machine_no]
    print("=" * 70)
    print(f"THIẾT LẬP CẤU HÌNH CHO MÁY {machine_no} ({', '.join(a['id'] for a in agents)})")
    print("=" * 70)

    generated_tokens = {}

    for agent_info in agents:
        agent_dir = ROOT_DIR / "agents" / agent_info["dir"]
        env_file = agent_dir / ".env"
        env_example_file = agent_dir / ".env.example"

        # Nếu thư mục agent chưa tồn tại, tạo khung cơ bản
        if not agent_dir.exists():
            print(f"[*] Thư mục {agent_dir} chưa có sẵn. Đang tạo...")
            agent_dir.mkdir(parents=True, exist_ok=True)

        token = secrets.token_urlsafe(32)
        if env_file.exists() and not args.overwrite:
            print(f"[!] File {env_file} đã tồn tại. Dùng cờ --overwrite để ghi đè.")
            # Đọc lại token hiện tại nếu có
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("AGENT_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            token = val
        else:
            env_content = generate_env_content(agent_info, token)
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(env_content)
            print(f"[OK] Đã tạo file cấu hình: agents/{agent_info['dir']}/.env")

        generated_tokens[agent_info["id"]] = token

        # Đảm bảo có .env.example
        if not env_example_file.exists():
            example_content = generate_env_content(agent_info, "your_secure_agent_token_here")
            with open(env_example_file, "w", encoding="utf-8") as f:
                f.write(example_content)

    ports_str = ",".join(str(a["port"]) for a in agents)
    print("\n" + "-" * 70)
    print("1. LỆNH MỞ TƯỜNG LỬA (FIREWALL) TRÊN MÁY NÀY:")
    print("-" * 70)
    print("• Trên Windows PowerShell (Run as Administrator):")
    print(f"  New-NetFirewallRule -DisplayName \"AI-Team Agents (Machine {machine_no})\" -Direction Inbound -LocalPort {ports_str} -Protocol TCP -Action Allow")
    print("\n• Trên Linux (UFW):")
    for a in agents:
        print(f"  sudo ufw allow {a['port']}/tcp")
    print("  sudo ufw reload")

    print("\n" + "-" * 70)
    print("2. KHÓA BẢO MẬT (COPY DÒNG SAU GỬI CHO PM ĐỂ ĐIỀN VÀO .env.lan):")
    print("-" * 70)
    for aid, tok in generated_tokens.items():
        env_var = f"AGENT_API_KEY_{aid.upper().replace('-', '_')}"
        print(f"{env_var}={tok}")

    print("\n" + "-" * 70)
    print("3. LỆNH KHỞI CHẠY CÁC AGENT TRÊN MÁY NÀY:")
    print("-" * 70)
    for a in agents:
        print(f"  # Agent {a['id']}:")
        print(f"  cd agents/{a['dir']} && python -m uvicorn api:app --host 0.0.0.0 --port {a['port']}")
    print("=" * 70)


def cmd_export_tokens(args: argparse.Namespace) -> None:
    """Quét các file .env trong agents/ và in ra định dạng .env.lan."""
    print("# Trích xuất AGENT_API_KEY từ các agent có trên máy này:")
    for m_no, agents in MACHINE_ROLES.items():
        for a in agents:
            env_file = ROOT_DIR / "agents" / a["dir"] / ".env"
            if env_file.exists():
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("AGENT_API_KEY="):
                            val = line.split("=", 1)[1].strip()
                            if val:
                                env_var = f"AGENT_API_KEY_{a['id'].upper().replace('-', '_')}"
                                print(f"{env_var}={val}")


def cmd_gen_endpoints(args: argparse.Namespace) -> None:
    """Tạo file scripts/endpoints.yaml dựa trên các địa chỉ IP cung cấp."""
    ip_map = {
        1: args.ip1 or "192.168.1.101",
        2: args.ip2 or "192.168.1.102",
        3: args.ip3 or "192.168.1.103",
        4: args.ip4 or "192.168.1.104",
        5: args.ip5 or "192.168.1.105",
    }

    yaml_lines = [
        "# scripts/endpoints.yaml",
        "# Bảng ánh xạ endpoint URL thực tế của 10 AI Agent trong mạng LAN / VPN",
        "# Được tạo tự động bởi scripts/configure_lan.py",
        "",
    ]

    for m_no, agents in MACHINE_ROLES.items():
        ip = ip_map[m_no]
        yaml_lines.append(f"# Máy {m_no} (IP: {ip})")
        for a in agents:
            yaml_lines.append(f"{a['id']}: \"http://{ip}:{a['port']}\"")
        yaml_lines.append("")

    target_file = ROOT_DIR / "scripts" / "endpoints.yaml"
    with open(target_file, "w", encoding="utf-8") as f:
        f.write("\n".join(yaml_lines))

    print(f"[OK] Đã tạo thành công file: {target_file}")
    print(target_file.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Công cụ thiết lập cấu hình kết nối mạng LAN cho AI Team")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: setup-machine
    p_setup = subparsers.add_parser("setup-machine", help="Sinh file .env và token cho 2 agent trên máy chỉ định (1..5)")
    p_setup.add_argument("machine", type=int, choices=[1, 2, 3, 4, 5], help="Số thứ tự máy (1 đến 5)")
    p_setup.add_argument("--overwrite", action="store_true", help="Ghi đè file .env nếu đã tồn tại")

    # Subcommand: export-tokens
    subparsers.add_parser("export-tokens", help="Xuất các biến token của agent cục bộ cho file .env.lan")

    # Subcommand: gen-endpoints
    p_endpoints = subparsers.add_parser("gen-endpoints", help="Sinh file scripts/endpoints.yaml từ địa chỉ IP")
    p_endpoints.add_argument("--ip1", type=str, default="192.168.1.101", help="Địa chỉ IP Máy 1 (mặc định: 192.168.1.101)")
    p_endpoints.add_argument("--ip2", type=str, default="192.168.1.102", help="Địa chỉ IP Máy 2 (mặc định: 192.168.1.102)")
    p_endpoints.add_argument("--ip3", type=str, default="192.168.1.103", help="Địa chỉ IP Máy 3 (mặc định: 192.168.1.103)")
    p_endpoints.add_argument("--ip4", type=str, default="192.168.1.104", help="Địa chỉ IP Máy 4 (mặc định: 192.168.1.104)")
    p_endpoints.add_argument("--ip5", type=str, default="192.168.1.105", help="Địa chỉ IP Máy 5 (mặc định: 192.168.1.105)")

    args = parser.parse_args()

    if args.command == "setup-machine":
        cmd_setup_machine(args)
    elif args.command == "export-tokens":
        cmd_export_tokens(args)
    elif args.command == "gen-endpoints":
        cmd_gen_endpoints(args)


if __name__ == "__main__":
    main()
