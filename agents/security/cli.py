import argparse
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

# Đảm bảo import đúng các module nội bộ của agent-template và in UTF-8
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from agent_contract.models import AgentIdentity, TaskRequest
from agent_contract.registry import CAPABILITY_REGISTRY
from agent import SecurityAgent
from config import AgentConfig
from llm import LLMClient


def create_default_agent(config: AgentConfig, mock: bool = False) -> SecurityAgent:
    """Khởi tạo SecurityAgent dựa trên config."""
    return SecurityAgent(config=config, llm=LLMClient(config=config, mock=mock))


def main() -> None:
    """CLI thực thi tác vụ hoặc truy vấn thông tin định danh của Agent."""
    parser = argparse.ArgumentParser(description="CLI chạy Agent độc lập tuân thủ Agent Contract v1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: identity
    subparsers.add_parser("identity", help="In thông tin AgentIdentity dưới dạng JSON")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Thực thi một tác vụ từ file TaskRequest JSON")
    run_parser.add_argument("task_file", type=str, help="Đường dẫn tới file TaskRequest JSON")
    run_parser.add_argument("--mock", action="store_true", help="Sử dụng LLM giả lập không gọi mạng")

    # Subcommand: prompt
    prompt_parser = subparsers.add_parser("prompt", help="Gửi câu lệnh prompt trực tiếp bằng văn bản")
    prompt_parser.add_argument("text", type=str, help="Nội dung câu prompt yêu cầu Agent thực hiện")
    prompt_parser.add_argument("--action", type=str, default="execute", help="Action cần thực hiện")
    prompt_parser.add_argument("--mock", action="store_true", help="Sử dụng LLM giả lập không gọi mạng")

    # Subcommand: chat
    chat_parser = subparsers.add_parser("chat", help="Mở phiên chat tương tác trực tiếp với Agent")
    chat_parser.add_argument("--mock", action="store_true", help="Sử dụng LLM giả lập không gọi mạng")

    args = parser.parse_args()
    config = AgentConfig()

    if args.command == "identity":
        agent = create_default_agent(config, mock=True)
        print(agent.get_identity().model_dump_json(indent=2))

    elif args.command == "run":
        task_path = Path(args.task_file)
        if not task_path.exists():
            print(f"Lỗi: Không tìm thấy file task tại '{task_path}'", file=sys.stderr)
            sys.exit(1)

        with open(task_path, "r", encoding="utf-8") as f:
            raw_json = f.read()

        request = TaskRequest.model_validate_json(raw_json)
        agent = create_default_agent(config, mock=args.mock)
        result = agent.handle(request)
        print(result.model_dump_json(indent=2))

    elif args.command == "prompt":
        now_tag = datetime.now(timezone.utc).strftime("%H%M%S")
        use_mock = args.mock or not bool(config.llm_api_key.strip())
        agent = create_default_agent(config, mock=use_mock)

        # Mặc định lấy capability đầu tiên nếu action="execute"
        action = args.action
        if action == "execute":
            action = next(iter(agent._actions))

        request = TaskRequest(
            task_id=f"TASK-CLI-{now_tag}",
            project_id="PROJ-CLI",
            agent_id=config.agent_id,
            role=config.agent_role,
            action=action,
            instruction=args.text,
            context={"source": "cli_prompt"},
        )

        mode_str = "MOCK LLM" if use_mock else f"REAL LLM ({config.llm_model})"
        print(f"[*] Đang gửi prompt tới Agent qua {mode_str}...")
        print(f"[*] Action: {action}")
        print(f"[*] Prompt: \"{args.text}\"\n")

        result = agent.handle(request)

        print("=" * 70)
        print(f"KẾT QUẢ THỰC THI: {result.status.value}")
        print(f"Tóm tắt: {result.summary}")
        if result.error:
            print(f"Chi tiết lỗi / lý do: {result.error.message} (Mã: {result.error.code.value})")
            questions = result.error.details.get("questions") if result.error.details else None
            if questions:
                print("Các câu hỏi cần làm rõ:")
                for q in questions:
                    print(f"  ? {q}")
        if result.artifacts:
            print("Artifacts tạo ra:")
            for art in result.artifacts:
                print(f"  + {art.name} ({art.type.value}): {art.uri}")
        if result.next_actions:
            print("Gợi ý bước kế tiếp:")
            for na in result.next_actions:
                print(f"  + {na.suggested_role.value} -> {na.action}: {na.reason}")
        print("=" * 70)
        print("\nJSON Chi tiết:")
        print(result.model_dump_json(indent=2))

    elif args.command == "chat":
        use_mock = args.mock or not bool(config.llm_api_key.strip())
        agent = create_default_agent(config, mock=use_mock)
        mode_str = "MOCK LLM" if use_mock else f"REAL LLM ({config.llm_model})"
        default_action = next(iter(agent._actions))
        current_action = default_action

        print("=" * 70)
        print(f"💬 {config.agent_role.value} AGENT INTERACTIVE CONSOLE [{mode_str}]")
        print("Nhập prompt trực tiếp để Agent xử lý. Gõ 'exit' hoặc 'quit' để thoát.")
        print("Lệnh đặc biệt: ':action <tên_action>' để chuyển đổi action.")
        print(f"Action hiện tại: {current_action}")
        print("=" * 70)

        counter = 1
        while True:
            try:
                user_input = input(f"\n[{counter}] Bạn > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nĐã thoát chế độ chat.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Tạm biệt!")
                break
            if user_input.startswith(":action"):
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    current_action = parts[1].strip()
                    print(f"[*] Đã chuyển action sang: {current_action}")
                else:
                    print(f"[*] Action hiện tại: {current_action}")
                continue

            now_tag = datetime.now(timezone.utc).strftime("%H%M%S")
            request = TaskRequest(
                task_id=f"TASK-CHAT-{now_tag}-{counter}",
                project_id="PROJ-CHAT",
                agent_id=config.agent_id,
                role=config.agent_role,
                action=current_action,
                instruction=user_input,
                context={"source": "cli_chat"},
            )
            print(f"[*] Đang xử lý action '{current_action}' với {mode_str}...")
            result = agent.handle(request)

            print(f"\nAgent [{result.status.value}] > {result.summary}")
            if result.error:
                print(f"⚠️  Lý do: {result.error.message}")
                questions = result.error.details.get("questions") if result.error.details else None
                if questions:
                    print("❓ Agent cần bạn cung cấp thêm thông tin:")
                    for q in questions:
                        print(f"   • {q}")
            if result.artifacts:
                print("📁 Artifacts vừa tạo:")
                for art in result.artifacts:
                    print(f"   • {art.name} ({art.type.value}): {art.uri}")
            if result.next_actions:
                print("👉 Bước kế tiếp gợi ý:")
                for na in result.next_actions:
                    print(f"   • [{na.suggested_role.value}] {na.action}: {na.reason}")
            counter += 1


if __name__ == "__main__":
    main()

