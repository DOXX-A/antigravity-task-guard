import json
import sys
from task_state import (
    analyze_transcript,
    clean_str,
    get_backoff_duration,
    parse_bool,
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

        # 周期性 Cron 任务或常驻定时任务不纳入单次看门狗劫持范围
        has_cron = bool(clean_str(args.get("CronExpression", "")))
        is_daemon = parse_bool(args.get("IsDaemon", False))
        if has_cron or is_daemon:
            print(json.dumps({"decision": "allow"}))
            return

        transcript_path = resolve_transcript_path(payload)
        active_cmds, active_timers, active_subs = analyze_transcript(transcript_path)

        # 1. 若当前已有正在运行的看门狗定时器，避免重复叠加定时器
        if active_timers:
            timer_ids = ", ".join(active_timers.keys())
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": (
                            f"【Hook 看门狗拦截】当前已有活动的看门狗定时器 ({timer_ids}) 在运行中，"
                            "无需重复调用 schedule。请直接结束本轮回复（不要再调用任何工具），等待系统通知唤醒。"
                        ),
                    }
                )
            )
            return

        # 2. 若当前没有后台终端命令任务，仅有子代理 (Subagent) 在运行：免除监控，拒绝无意义的轮询定时
        if not active_cmds and active_subs:
            sub_ids = ", ".join(active_subs.keys())
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": (
                            f"【Hook 看门狗提示】当前后台仅有子代理 ({sub_ids}) 在运行，无后台终端命令任务 (run_command)。"
                            "子代理具备健壮的退出与消息通知机制，无需设置 schedule 定时器监控。"
                            "请直接结束本轮回复（不再调用工具），系统将在子代理完成任务或发来消息时自动唤醒你。"
                        ),
                    }
                )
            )
            return

        # 3. 若当前没有后台终端命令任务且无子代理（例如用户显式要求设置一个普通一次性提醒闹钟），正常放行
        if not active_cmds:
            print(json.dumps({"decision": "allow"}))
            return

        # 4. 存在活动的后台 run_command 任务：
        #    检查是否上一轮看门狗定时器已到期但模型尚未调用 manage_task(status/list) 检查任务生死状态
        uninspected_tasks = [
            tid for tid, info in active_cmds.items() if info.get("timer_expired_uninspected", False)
        ]
        if uninspected_tasks:
            tids_str = ", ".join(uninspected_tasks)
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": (
                            f"【Hook 看门狗拦截】上一轮看门狗定时器已到期，在重新调用 schedule 延长等待之前，"
                            f"请先调用 manage_task(Action='status', TaskId='{uninspected_tasks[0]}') 检查后台任务 ({tids_str}) 的最新状态与日志输出！"
                            "若发现任务已执行完毕但未退出（管道 EOF 丢失）或卡死报错，请调用 manage_task(Action='kill') 杀死假死任务；"
                            "若确认任务仍在正常推进，检查后再调用 schedule。"
                        ),
                    }
                )
            )
            return

        # 5. 自动化接管计时逻辑：根据当前活动任务已历经的看门狗轮次，自动按指数退避设定 DurationSeconds
        max_schedule_count = max(info.get("schedule_count", 0) for info in active_cmds.values())
        auto_duration = get_backoff_duration(max_schedule_count)
        next_duration = get_backoff_duration(max_schedule_count + 1)
        active_task_ids = ", ".join(active_cmds.keys())
        round_num = max_schedule_count + 1

        auto_prompt = (
            f"【看门狗定时检查 #{round_num}（本轮等待 {auto_duration}s 已到期）】"
            f"后台终端命令任务 ({active_task_ids}) 仍未返回结束信号。"
            f"请立即调用 manage_task(Action='status') 检查任务状态与最新日志输出："
            f"(1) 若日志显示任务实际上已执行完毕（如打印了完成标志但因管道 EOF 丢失未退出）或进程已死锁/假死，"
            f"请立即调用 manage_task(Action='kill') 杀死该任务；"
            f"(2) 若日志显示任务仍在正常推进，请再次调用 schedule（无需关心时长参数，Hook 将自动为您延长下一轮等待至 {next_duration}s），"
            f"然后立即结束本轮回复等待唤醒。"
        )

        resp = {
            "decision": "allow",
            "overwrite": {
                "DurationSeconds": auto_duration,
                "TimerCondition": "any",
                "Prompt": auto_prompt,
            },
        }
        print(json.dumps(resp))
    except Exception:
        print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    main()
