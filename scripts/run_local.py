import argparse
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import yaml

def _setup_console_encoding() -> None:
    """Đảm bảo UTF-8 trên Windows console khi chạy trực tiếp từ dòng lệnh."""
    if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "scripts" / "local_agents.yaml"
STATE_FILE_PATH = ROOT_DIR / "scripts" / ".run_local_state.json"
LOGS_DIR = ROOT_DIR / "logs"


def load_yaml_config(config_path: Path) -> list[dict[str, Any]]:
    """Đọc và giải mã file cấu hình YAML chứa danh sách Agent."""
    if not config_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file cấu hình: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "agents" not in data:
        raise ValueError(f"Cấu trúc file YAML không hợp lệ: thiếu khóa 'agents'")

    agents = data.get("agents", [])
    if not isinstance(agents, list):
        raise ValueError("Khóa 'agents' trong file YAML phải là một danh sách.")

    return agents


def validate_preflight(agents: list[dict[str, Any]], root_dir: Path = ROOT_DIR) -> None:
    """Kiểm tra điều kiện tiên quyết: không trùng port, mỗi agent có file .env."""
    seen_ports: set[int] = set()
    for item in agents:
        name = item.get("name")
        port = item.get("port")
        path_str = item.get("path")

        if not name or port is None or not path_str:
            raise ValueError(f"Agent thiếu trường bắt buộc (name, path, port): {item}")

        try:
            port_num = int(port)
        except ValueError:
            raise ValueError(f"Port của agent '{name}' không phải là số hợp lệ: {port}")

        if port_num in seen_ports:
            raise ValueError(f"Phát hiện trùng lặp PORT: {port_num} được gán cho nhiều agent.")
        seen_ports.add(port_num)

        agent_dir = (root_dir / path_str).resolve()
        if not agent_dir.exists() or not agent_dir.is_dir():
            raise FileNotFoundError(f"Thư mục agent '{name}' không tồn tại: {agent_dir}")

        env_file = agent_dir / ".env"
        if not env_file.exists() or not env_file.is_file():
            raise FileNotFoundError(
                f"Agent '{name}' thiếu file .env tại {env_file}. Vui lòng tạo file .env từ .env.example trước khi chạy."
            )


def check_agent_health(host: str, port: int, timeout_sec: float = 2.0) -> dict[str, Any] | None:
    """Thực hiện gọi HTTP GET /health tới agent và phân tích kết quả JSON."""
    url = f"http://{host}:{port}/health"
    try:
        with urlopen(url, timeout=timeout_sec) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                return json.loads(body)
    except Exception:
        return None
    return None


def terminate_process_tree(pid: int) -> None:
    """Dừng tiến trình và mọi tiến trình con của nó an toàn trên cả Windows và Linux."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=5,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def save_state(state: dict[str, Any]) -> None:
    """Lưu thông tin tiến trình đang chạy vào file state."""
    STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_state() -> dict[str, Any]:
    """Đọc thông tin trạng thái tiến trình từ file state."""
    if not STATE_FILE_PATH.exists():
        return {}
    try:
        with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def clear_state() -> None:
    """Xóa file lưu trạng thái khi đã dừng toàn bộ."""
    if STATE_FILE_PATH.exists():
        try:
            STATE_FILE_PATH.unlink()
        except OSError:
            pass


def cmd_up(
    config_path: Path = DEFAULT_CONFIG_PATH,
    foreground: bool = True,
    timeout_limit: float = 15.0,
) -> int:
    """Khởi động toàn bộ Agent được khai báo trong YAML thành các tiến trình độc lập."""
    print(f"[*] Đang nạp cấu hình từ: {config_path}")
    try:
        agents = load_yaml_config(config_path)
        validate_preflight(agents, ROOT_DIR)
    except Exception as e:
        print(f"[!] Lỗi kiểm tra trước khi chạy: {e}", file=sys.stderr)
        return 1

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    started_processes: dict[str, dict[str, Any]] = {}
    file_handles = []

    def stop_all() -> None:
        print("\n[*] Đang dừng toàn bộ các Agent con...")
        for name, info in list(started_processes.items()):
            pid = info.get("pid")
            if pid:
                terminate_process_tree(pid)
        for h in file_handles:
            try:
                h.close()
            except Exception:
                pass
        clear_state()
        print("[*] Đã dừng sạch mọi tiến trình Agent.")

    print(f"[*] Bắt đầu khởi động {len(agents)} Agent...")
    try:
        for item in agents:
            name = item["name"]
            port = int(item["port"])
            agent_path = (ROOT_DIR / item["path"]).resolve()
            log_file = LOGS_DIR / f"{name}.log"

            # Thiết lập biến môi trường riêng: ghi đè PORT, không sửa file .env
            env_vars = os.environ.copy()
            env_vars["PORT"] = str(port)

            # Mở file ghi log cho agent
            log_handle = open(log_file, "a", encoding="utf-8")
            file_handles.append(log_handle)

            # Khởi động uvicorn server qua subprocess không shell
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "api:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(port),
            ]

            creation_flags = 0
            if sys.platform == "win32" and not foreground:
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

            proc = subprocess.Popen(
                cmd,
                cwd=str(agent_path),
                env=env_vars,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creation_flags,
            )

            started_processes[name] = {
                "pid": proc.pid,
                "port": port,
                "path": str(agent_path),
                "log_file": str(log_file),
                "process": proc,
            }
            print(f"  + Agent '{name}': PID={proc.pid}, PORT={port}, log={log_file.name}")

        # Kiểm tra /health trong 15 giây cho toàn bộ Agent
        print("[*] Đang chờ xác nhận /health từ tất cả các Agent (tối đa 15 giây)...")
        start_time = time.time()
        timeout_limit = 15.0
        health_status: dict[str, dict[str, Any]] = {}

        while time.time() - start_time < timeout_limit:
            all_healthy = True
            for item in agents:
                name = item["name"]
                if name in health_status:
                    continue
                port = int(item["port"])
                health = check_agent_health("127.0.0.1", port, timeout_sec=1.0)
                if health:
                    health_status[name] = health
                else:
                    all_healthy = False

            if len(health_status) == len(agents):
                break
            time.sleep(0.5)

        # Nếu có agent không vượt qua health check
        missing = [a["name"] for a in agents if a["name"] not in health_status]
        if missing:
            print(f"[!] KHỞI ĐỘNG THẤT BẠI: Các agent sau không phản hồi /health sau {timeout_limit}s: {', '.join(missing)}")
            for m in missing:
                log_p = LOGS_DIR / f"{m}.log"
                if log_p.exists():
                    print(f"--- Log cuối cùng của {m} ({log_p.name}): ---")
                    lines = log_p.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines[-10:]:
                        print(f"  {line}")
                    print("------------------------------------------")
            stop_all()
            return 1

        # Lưu state ra file để lệnh status và down từ terminal khác có thể sử dụng
        state_data = {
            name: {
                "pid": info["pid"],
                "port": info["port"],
                "path": info["path"],
                "log_file": info["log_file"],
            }
            for name, info in started_processes.items()
        }
        save_state(state_data)

        # In bảng trạng thái thành công
        print("\n==========================================================================================")
        print(" TOÀN BỘ CÁC AGENT ĐÃ KHỞI ĐỘNG THÀNH CÔNG VÀ SẴN SÀNG")
        print("==========================================================================================")
        print(f"| {'NAME':<12} | {'AGENT_ID':<12} | {'ROLE':<10} | {'PORT':<6} | {'STATUS':<8} | {'CONTRACT':<10} |")
        print(f"|{'-'*14}|{'-'*14}|{'-'*12}|{'-'*8}|{'-'*10}|{'-'*12}|")
        for item in agents:
            name = item["name"]
            h = health_status.get(name, {})
            agent_id = h.get("agent_id", "N/A")
            role = h.get("role", "N/A")
            status = h.get("status", "healthy")
            ver = h.get("contract_version", "1.0.0")
            print(f"| {name:<12} | {agent_id:<12} | {role:<10} | {item['port']:<6} | {status:<8} | {ver:<10} |")
        print("==========================================================================================")

        if not foreground:
            return 0

        print("\n[*] Các Agent đang chạy nền. Nhấn Ctrl+C để dừng toàn bộ...")
        while True:
            # Kiểm tra xem có tiến trình nào bị chết đột ngột không
            for name, info in list(started_processes.items()):
                proc = info["process"]
                if proc.poll() is not None:
                    print(f"[!] Cảnh báo: Agent '{name}' (PID={proc.pid}) đã tự thoát với exit code {proc.returncode}!")
                    stop_all()
                    return 1
            time.sleep(1.0)

    except KeyboardInterrupt:
        stop_all()
        return 0
    except Exception as e:
        print(f"[!] Gặp lỗi bất ngờ: {e}", file=sys.stderr)
        stop_all()
        return 1


def cmd_status(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """Kiểm tra và in bảng trạng thái chi tiết của tất cả các Agent qua endpoint /health."""
    state = load_state()
    try:
        agents = load_yaml_config(config_path)
    except Exception as e:
        print(f"[!] Không thể đọc cấu hình: {e}")
        return 1

    print("==========================================================================================")
    print(" TRẠNG THÁI HIỆN TẠI CỦA CÁC AGENT (Agent Contract v1.0)")
    print("==========================================================================================")
    print(f"| {'NAME':<12} | {'AGENT_ID':<12} | {'ROLE':<10} | {'PORT':<6} | {'HEALTH':<10} | {'PID':<8} |")
    print(f"|{'-'*14}|{'-'*14}|{'-'*12}|{'-'*8}|{'-'*12}|{'-'*10}|")

    all_ok = True
    for item in agents:
        name = item["name"]
        port = int(item["port"])
        pid = state.get(name, {}).get("pid", "N/A")
        h = check_agent_health("127.0.0.1", port, timeout_sec=2.0)
        if h:
            agent_id = h.get("agent_id", "N/A")
            role = h.get("role", "N/A")
            status = h.get("status", "healthy")
            print(f"| {name:<12} | {agent_id:<12} | {role:<10} | {port:<6} | {status:<10} | {pid:<8} |")
        else:
            all_ok = False
            print(f"| {name:<12} | {'DOWN':<12} | {'UNKNOWN':<10} | {port:<6} | {'UNREACHABLE':<10} | {pid:<8} |")

    print("==========================================================================================")
    return 0 if all_ok else 1


def cmd_down() -> int:
    """Dừng tất cả các tiến trình Agent đang chạy được ghi nhận trong state."""
    state = load_state()
    if not state:
        print("[*] Không có Agent nào đang được ghi nhận chạy trong state.")
        return 0

    print(f"[*] Đang dừng {len(state)} Agent...")
    for name, info in state.items():
        pid = info.get("pid")
        if pid:
            print(f"  - Đang dừng Agent '{name}' (PID: {pid})...")
            terminate_process_tree(pid)

    clear_state()
    print("[*] Đã dừng thành công toàn bộ các Agent.")
    return 0


def main() -> None:
    _setup_console_encoding()
    parser = argparse.ArgumentParser(
        description="Script điều phối chạy cụm Agent độc lập tại cục bộ (local_agents.yaml)."
    )
    parser.add_argument(
        "action",
        choices=["up", "status", "down"],
        help="Thao tác: up (khởi động), status (xem trạng thái), down (dừng toàn bộ)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Đường dẫn file cấu hình YAML (mặc định: scripts/local_agents.yaml)",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Chạy ở chế độ nền (không chặn console, dùng cho up)",
    )

    args = parser.parse_args()
    config_p = Path(args.config).resolve()

    if args.action == "up":
        exit_code = cmd_up(config_p, foreground=not args.background)
        sys.exit(exit_code)
    elif args.action == "status":
        exit_code = cmd_status(config_p)
        sys.exit(exit_code)
    elif args.action == "down":
        exit_code = cmd_down()
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
