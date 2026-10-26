import json
import sys
from task_state import (
    analyze_transcript,
    clean_str,
    get_backoff_duration,
    resolve_transcript_path,
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

        action = clean_str(args.get("Action", "")).lower()
        target_tid = clean_str(args.get("TaskId", ""))

        # 始终无条件放行 kill（杀死假死任务）、send_input 与 list
        if action in ("kill", "send_input", "list"):
            print(json.dumps({"decision": "allow"}))
            return

        if action == "status":
            transcript_path = resolve_transcript_path(payload)
            active_cmds, active_timers, _ = analyze_transcript(transcript_path)

            # 1. 若当前本轮已经挂载了活动的看门狗定时器，阻止模型在挂载定时器后继续调用 status 破坏休眠
            if active_timers:
                print(
                    json.dumps(
                        {
                            "decision": "deny",
                            "reason": (
                                "【Hook 看门狗拦截】当前看门狗定时器已在后台运行中。"
                                "调用 schedule 后请勿再调用 manage_task(Action='status')，请直接结束本轮回复（不再调用任何工具），"
                                "等待后台任务完成或定时器到期自动唤醒！"
                                "（注：若你确定任务已假死需立即终止，可直接调用 manage_task(Action='kill')）。"
                            ),
                        }
                    )
                )
                return

            # 2. 防止模型在同一唤醒周期内对同一个仍在运行的任务连续死循环调用 status 而不挂载 schedule
            if target_tid in active_cmds:
                t_info = active_cmds[target_tid]
                checks_done = t_info.get("status_checks_since_last_timer", 0)
                if checks_done >= 1:
                    next_dur = get_backoff_duration(t_info.get("schedule_count", 0))
                    print(
                        json.dumps(
                            {
                                "decision": "deny",
                                "reason": (
                                    f"【Hook 看门狗拦截】本轮已调用过 manage_task(Action='status') 检查过任务 {target_tid}。"
                                    "请根据刚才获取到的任务日志做出决策："
                                    "(1) 若任务已死锁/假死或已完成输出但进程未退出，请直接调用 manage_task(Action='kill') 杀死它；"
                                    f"(2) 若任务仍在正常推进，严禁原地连续轮询 status，请立即调用 schedule 工具"
                                    f"（Hook 将自动为您设定 {next_dur}s 退避定时器）并结束本轮回复！"
                                ),
                            }
                        )
                    )
                    return

        print(json.dumps({"decision": "allow"}))
    except Exception:
        print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()
