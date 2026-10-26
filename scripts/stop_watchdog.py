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
            print(json.dumps({"decision": ""}))
            return

        payload = json.loads(input_data)
        fully_idle = payload.get("fullyIdle", True)
        termination_reason = clean_str(payload.get("terminationReason", ""))

        # 1. 若用户主动取消或因异常/配额上限终止，不拦截停止
        if termination_reason and termination_reason != "NO_TOOL_CALL":
            print(json.dumps({"decision": ""}))
            return

        # 2. 若系统底层确认没有任何后台任务或子代理，正常放行停止
        if fully_idle:
            print(json.dumps({"decision": ""}))
            return

        transcript_path = resolve_transcript_path(payload)
        if not transcript_path:
            print(json.dumps({"decision": ""}))
            return

        active_cmds, active_timers, _ = analyze_transcript(transcript_path)

        # 3. 若当前没有活动的非守护 run_command 终端任务（例如后台仅有子代理 Subagent 或常驻 Daemon 服务），直接放行停止，无需监控子代理
        if not active_cmds:
            print(json.dumps({"decision": ""}))
            return

        # 4. 若当前已有活动的 schedule 看门狗定时器在运行，允许静默休眠等待唤醒
        if active_timers:
            print(json.dumps({"decision": ""}))
            return

        # 5. 存在未挂载看门狗的活动 run_command 任务，拦截休眠并按当前状态精准指引模型
        active_task_ids = ", ".join(active_cmds.keys())
        first_tid = next(iter(active_cmds.keys()))
        max_schedule_count = max(info.get("schedule_count", 0) for info in active_cmds.values())
        next_duration = get_backoff_duration(max_schedule_count)
        uninspected_tasks = [
            tid for tid, info in active_cmds.items() if info.get("timer_expired_uninspected", False)
        ]

        if uninspected_tasks:
            reason = (
                f"【Hook 看门狗安全拦截】后台终端命令任务 ({active_task_ids}) 仍在运行，且上一轮定时器已到期。"
                f"请先调用 manage_task(Action='status', TaskId='{uninspected_tasks[0]}') 检查任务状态与日志输出："
                "若任务已假死或管道死锁，请调用 manage_task(Action='kill') 将其终止；"
                f"若任务仍在正常执行，检查后请调用 schedule 工具启动下一轮看门狗（无需计算时长，Hook 将自动为您设定为 {next_duration}s 及 TimerCondition='any'）！"
            )
        else:
            reason = (
                f"【Hook 看门狗安全拦截】检测到后台仍有正在运行的终端命令任务 ({active_task_ids})，但当前未挂载活动的 schedule 看门狗定时器。"
                f"若刚才的日志表明任务已假死或已完成输出但未退出，请调用 manage_task(Action='kill', TaskId='{first_tid}') 终止任务；"
                f"若任务正在正常执行，请立即调用 schedule 工具挂载看门狗（随便传入任意 DurationSeconds 即可，Hook 将自动为您接管并设定为 {next_duration}s 及 TimerCondition='any'），"
                "调用 schedule 后直接结束回复进入休眠！"
            )

        print(json.dumps({"decision": "continue", "reason": reason}))
    except Exception:
        # 异常兜底放行
        print(json.dumps({"decision": ""}))


if __name__ == "__main__":
    main()
