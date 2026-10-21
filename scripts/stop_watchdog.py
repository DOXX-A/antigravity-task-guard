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
    Check if since the latest user input (USER_INPUT):
    1. A schedule tool call was made
    2. A run_command was marked as a long-running daemon (IsDaemon=True)
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return False, False

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        has_schedule = False
        has_daemon = False

        # Traverse backwards to find the start of the current turn
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

        # If no background tasks are running, allow normal termination
        if fully_idle:
            print(json.dumps({"decision": ""}))
            return

        # Fallback transcript path resolution
        if not transcript_path or not os.path.exists(transcript_path):
            conv_id = payload.get("conversationId", "")
            if conv_id:
                alt_path = os.path.expanduser(
                    rf"~/.gemini/antigravity/brain/{conv_id}/.system_generated/logs/transcript.jsonl"
                )
                if os.path.exists(alt_path):
                    transcript_path = alt_path

        has_schedule, has_daemon = check_watchdog_status(transcript_path)

        # Allow stopping if a watchdog timer is active or the task is a daemon
        if has_schedule or has_daemon:
            print(json.dumps({"decision": ""}))
            return

        # Intercept termination if unmonitored background tasks exist
        resp = {
            "decision": "continue",
            "reason": (
                "[Hook Watchdog Intercept] Active background task(s) detected, "
                "but no schedule watchdog timer was set in this turn. "
                "To prevent unmonitored hangs or pipe deadlocks, please check if the task is running normally "
                "or call the schedule tool to set an appropriate watchdog timer."
            )
        }
        print(json.dumps(resp))
    except Exception:
        # Fallback to allow stopping on unexpected errors
        print(json.dumps({"decision": ""}))

if __name__ == "__main__":
    main()
