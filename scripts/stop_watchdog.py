import json
import sys
import os

def parse_bool(val, default=False):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return default

def check_watchdog_status(transcript_path):
    """
    检查自上一次用户输入 (USER_INPUT) 以来：
    1. 是否调用了 schedule 定时器
    2. 是否有标记为常驻服务 (IsDaemon=True) 的 run_command
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return False, False

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        has_schedule = False
        has_daemon = False

        # 倒序遍历直到最新的 USER_INPUT
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            step_type = data.get("type", "")
            if step_type == "USER_INPUT":
                # 已经回溯到了当前轮次的起点
                break

            tool_calls = data.get("tool_calls", [])
            for call in tool_calls:
                name = call.get("name", "")
                if name == "schedule":
                    has_schedule = True
                elif name == "run_command":
                    args = call.get("args", {})
                    if parse_bool(args.get("IsDaemon", False)):
                        has_daemon = True

        return has_schedule, has_daemon
    except Exception:
        return False, False

def main():
    try:
        input_data = sys.stdin.read()
        if not input_data:
            print(json.dumps({"decision": ""}))
            return

        payload = json.loads(input_data)
        fully_idle = payload.get("fullyIdle", True)
        transcript_path = payload.get("transcriptPath", "")

        # 如果没有后台任务在运行，允许正常退出
        if fully_idle:
            print(json.dumps({"decision": ""}))
            return

        # 检查 transcript 路径兜底
        if not transcript_path or not os.path.exists(transcript_path):
            conv_id = payload.get("conversationId", "")
            if conv_id:
                alt_path = os.path.expanduser(
                    rf"~/.gemini/antigravity/brain/{conv_id}/.system_generated/logs/transcript.jsonl"
                )
                if os.path.exists(alt_path):
                    transcript_path = alt_path

        has_schedule, has_daemon = check_watchdog_status(transcript_path)

        # 若已设置看门狗定时器或为常驻守护进程，允许挂起休眠
        if has_schedule or has_daemon:
            print(json.dumps({"decision": ""}))
            return

        # 未设置看门狗定时器且非常驻服务，强行拦截终止行为
        resp = {
            "decision": "continue",
            "reason": (
                "【Hook 看门狗安全拦截】检测到后台仍有正在运行的任务（fullyIdle=false），"
                "但本轮交互尚未调用 schedule 设置看门狗定时器。"
                "为防止底层终端管道死锁或进程卡死导致会话永久挂起，"
                "请立即调用 schedule 工具设置一个合理的超时看门狗定时器（建议 60-120 秒）！"
            )
        }
        print(json.dumps(resp))
    except Exception:
        # 异常兜底放行
        print(json.dumps({"decision": ""}))

if __name__ == "__main__":
    main()
