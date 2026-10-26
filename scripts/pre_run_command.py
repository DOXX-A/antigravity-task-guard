import json
import re
import sys
from task_state import (
    analyze_transcript,
    clean_str,
    parse_bool,
    parse_int,
    resolve_transcript_path,
)

PURE_SLEEP_PATTERN = re.compile(
    r"^\s*(?:(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|cmd(?:\.exe)?|bash|sh)\s+(?:-[a-zA-Z]+\s+|/[cC]\s+)*)?"
    r"[\"']?\s*(?:Start-Sleep(?:\s+-Seconds|\s+-Milliseconds|\s+-s|\s+-m)?|sleep|timeout(?:\.exe)?\s+/t)\s+\d+(?:\.\d+)?\s*[\"']?\s*$",
    re.IGNORECASE,
)


def main():
    try:
        input_data = sys.stdin.read()
        if not input_data:
            print(json.dumps({"decision": "allow"}))
            return

        payload = json.loads(input_data)
        tool_call = payload.get("toolCall", {})
        args = tool_call.get("args", {})

        cmd_line = clean_str(args.get("CommandLine", ""))
        is_daemon = parse_bool(args.get("IsDaemon", False))
        wait_ms = parse_int(args.get("WaitMsBeforeAsync", 0))

        # 拦截模型使用纯 sleep 命令充当定时器的反模式行为（仅在已有后台任务或子代理运行时拦截）
        if PURE_SLEEP_PATTERN.match(cmd_line):
            transcript_path = resolve_transcript_path(payload)
            active_cmds, _, active_subs = analyze_transcript(transcript_path)
            if active_cmds or active_subs:
                print(
                    json.dumps(
                        {
                            "decision": "deny",
                            "reason": (
                                "【Hook 看门狗拦截】严禁使用终端 sleep 命令充当轮询定时器！"
                                "若需等待后台命令任务，请直接调用 schedule 工具（Hook 会自动配置合理的退避计时器）并结束回复；"
                                "若仅在等待子代理（Subagent），请直接结束本轮回复等待子代理自动唤醒。"
                            ),
                        }
                    )
                )
                return

        overwrites = {}
        # 对非守护进程：若同步等待阈值低于 10000ms（系统上限），自动提升至 10000ms，从源头避免短任务过早降级进入后台
        if not is_daemon and wait_ms < 10000:
            overwrites["WaitMsBeforeAsync"] = 10000

        resp = {"decision": "allow"}
        if overwrites:
            resp["overwrite"] = overwrites

        print(json.dumps(resp))
    except Exception:
        print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()
