import json
import os
import re

# 动态退避等待时长阶梯（秒）：30s -> 60s -> 120s -> 240s -> 480s -> 600s（上限10分钟）
BACKOFF_SCHEDULE = [30, 60, 120, 240, 480, 600]


def parse_int(val, default=0):
    try:
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            clean = val.strip().strip('"').strip("'")
            return int(float(clean))
    except Exception:
        pass
    return default


def parse_bool(val, default=False):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().strip('"').strip("'").lower() in ("true", "1", "yes")
    return default


def clean_str(val):
    if val is None:
        return ""
    return str(val).strip().strip('"').strip("'")


def get_backoff_duration(schedule_count):
    idx = max(0, min(int(schedule_count), len(BACKOFF_SCHEDULE) - 1))
    return BACKOFF_SCHEDULE[idx]


def resolve_transcript_path(payload):
    transcript_path = payload.get("transcriptPath", "")
    if transcript_path and os.path.exists(transcript_path):
        return transcript_path

    conv_id = payload.get("conversationId", "")
    if conv_id:
        for base in ("antigravity", "antigravity-cli", "antigravity-ide"):
            alt_path = os.path.expanduser(
                rf"~/.gemini/{base}/brain/{conv_id}/.system_generated/logs/transcript.jsonl"
            )
            if os.path.exists(alt_path):
                return alt_path
    return ""


def load_transcript_lines(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return []

    full_path = os.path.join(os.path.dirname(transcript_path), "transcript_full.jsonl")
    target_path = full_path if os.path.exists(full_path) else transcript_path

    try:
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()
    except Exception:
        return []


def analyze_transcript(transcript_path):
    """
    精确重建当前会话的后台任务状态机：
    - active_cmd_tasks: 当前本轮仍在后台运行的非守护 run_command 终端任务
    - active_timers: 当前本轮仍在倒计时的 schedule 看门狗定时器
    - active_subagents: 当前仍在运行的子代理 (invoke_subagent)
    """
    lines = load_transcript_lines(transcript_path)
    active_cmd_tasks = {}
    active_timers = {}
    active_subagents = {}
    pending_calls = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue

        step_idx = d.get("step_index", 0)
        step_type = d.get("type", "")
        content = str(d.get("content", ""))

        # 新一轮用户输入开始时，重置上一轮遗留的未闭合命令与定时器状态，防止跨轮次陈旧状态污染
        if step_type == "USER_INPUT":
            active_cmd_tasks.clear()
            active_timers.clear()
            pending_calls = []
            continue

        if step_type == "PLANNER_RESPONSE":
            pending_calls = list(d.get("tool_calls", []))
            continue

        if step_type == "GENERIC":
            call = pending_calls.pop(0) if pending_calls else {}
            tool_name = call.get("name", "")
            tool_args = call.get("args", {}) if isinstance(call.get("args"), dict) else {}

            # 1. 检测转入后台运行的任务 (run_command 或 schedule)
            m_bg = re.search(r"Tool is running as a background task with task id:\s*(\S+)", content)
            if m_bg:
                tid = m_bg.group(1)
                is_timer_desc = bool(re.search(r"Task Description:\s*(Timer|Cron):", content))
                if tool_name == "schedule" or is_timer_desc:
                    is_daemon = parse_bool(tool_args.get("IsDaemon", False))
                    has_cron = bool(clean_str(tool_args.get("CronExpression", "")))
                    if not is_daemon and not has_cron:
                        cond = clean_str(tool_args.get("TimerCondition", "never")) or "never"
                        dur = parse_int(tool_args.get("DurationSeconds", 0))
                        active_timers[tid] = {
                            "task_id": tid,
                            "step_index": step_idx,
                            "condition": cond,
                            "duration": dur,
                        }
                        for t_info in active_cmd_tasks.values():
                            t_info["schedule_count"] += 1
                            t_info["timer_expired_uninspected"] = False
                            t_info["status_checks_since_last_timer"] = 0
                elif tool_name == "run_command" or not is_timer_desc:
                    is_daemon = parse_bool(tool_args.get("IsDaemon", False))
                    if not is_daemon:
                        active_cmd_tasks[tid] = {
                            "task_id": tid,
                            "step_index": step_idx,
                            "schedule_count": 0,
                            "timer_expired_uninspected": False,
                            "status_checks_since_last_timer": 0,
                            "cmd": clean_str(tool_args.get("CommandLine", ""))[:120],
                        }

            # 2. 检测 manage_task 状态查询、列表与终止操作
            if tool_name == "manage_task" or "Task:" in content or "background task" in content:
                action = clean_str(tool_args.get("Action", "")).lower()
                target_tid = clean_str(tool_args.get("TaskId", ""))

                if (
                    action == "list"
                    or "No background tasks are currently running" in content
                    or "background task(s) currently running" in content
                ):
                    for t_info in active_cmd_tasks.values():
                        t_info["timer_expired_uninspected"] = False
                    if "No background tasks are currently running" in content:
                        active_cmd_tasks.clear()
                        active_timers.clear()
                    elif "background task(s) currently running" in content:
                        running_ids = set(re.findall(r'"taskId"\s*:\s*"([^"]+)"', content))
                        for tid in list(active_cmd_tasks.keys()):
                            if tid not in running_ids:
                                del active_cmd_tasks[tid]
                        for tid in list(active_timers.keys()):
                            if tid not in running_ids:
                                del active_timers[tid]

                if action == "status" or ("Task:" in content and "Status:" in content):
                    if target_tid in active_cmd_tasks:
                        active_cmd_tasks[target_tid]["timer_expired_uninspected"] = False
                        active_cmd_tasks[target_tid]["status_checks_since_last_timer"] += 1
                    m_st = re.search(r"Task:\s*(\S+)\s+Status:\s*(\S+)", content)
                    if m_st:
                        tid, st = m_st.group(1), m_st.group(2).upper()
                        if tid in active_cmd_tasks and tid != target_tid:
                            active_cmd_tasks[tid]["timer_expired_uninspected"] = False
                            active_cmd_tasks[tid]["status_checks_since_last_timer"] += 1
                        if st != "RUNNING":
                            active_cmd_tasks.pop(tid, None)
                            active_timers.pop(tid, None)

                if action == "kill" or "cancelled." in content:
                    if target_tid and (
                        "cancelled" in content.lower()
                        or "not found" in content.lower()
                        or "invalid" not in content.lower()
                    ):
                        active_cmd_tasks.pop(target_tid, None)
                        active_timers.pop(target_tid, None)
                    for m_kill in re.finditer(r'Task\s+"([^"]+)"\s+cancelled', content):
                        active_cmd_tasks.pop(m_kill.group(1), None)
                        active_timers.pop(m_kill.group(1), None)

            # 3. 检测子代理生命周期 (invoke_subagent / send_message / manage_subagents)
            if tool_name == "invoke_subagent" or "Created the following subagent:" in content:
                m_sub = re.search(r'"conversationId"\s*:\s*"([^"]+)"', content)
                if m_sub:
                    scid = m_sub.group(1)
                    active_subagents[scid] = {
                        "conversation_id": scid,
                        "step_index": step_idx,
                        "state": "running",
                    }
            elif tool_name == "send_message":
                recip = clean_str(tool_args.get("Recipient", ""))
                if recip:
                    active_subagents[recip] = {
                        "conversation_id": recip,
                        "step_index": step_idx,
                        "state": "running",
                    }
            elif tool_name == "manage_subagents" or "active subagent(s):" in content:
                sub_action = clean_str(tool_args.get("Action", "")).lower()
                if sub_action == "kill_all" or "No active subagents" in content:
                    active_subagents.clear()
                elif sub_action == "kill":
                    cids = tool_args.get("ConversationIds", [])
                    if isinstance(cids, list):
                        for c in cids:
                            active_subagents.pop(clean_str(c), None)
                elif "active subagent(s):" in content:
                    running_subs = set()
                    for m_s in re.finditer(
                        r'"conversationId"\s*:\s*"([^"]+)"[^}]*"state"\s*:\s*"([^"]+)"',
                        content,
                    ):
                        scid, sstate = m_s.group(1), m_s.group(2)
                        if sstate == "running":
                            running_subs.add(scid)
                            active_subagents[scid] = {
                                "conversation_id": scid,
                                "step_index": step_idx,
                                "state": "running",
                            }
                    for scid in list(active_subagents.keys()):
                        if scid not in running_subs:
                            del active_subagents[scid]

        elif step_type == "SYSTEM_MESSAGE":
            for tid in re.findall(r'Task id "([^"]+)" finished', content):
                active_cmd_tasks.pop(tid, None)
                active_timers.pop(tid, None)

            senders = re.findall(r"\[Message\]\s+timestamp=\S+\s+sender=(\S+)", content)
            for sender in senders:
                if sender in active_cmd_tasks:
                    active_cmd_tasks.pop(sender, None)
                if sender in active_subagents:
                    active_subagents.pop(sender, None)
                if sender in active_timers:
                    active_timers.pop(sender, None)
                    for t_info in active_cmd_tasks.values():
                        t_info["timer_expired_uninspected"] = True
                        t_info["status_checks_since_last_timer"] = 0

                # 根据 TimerCondition 规则，当匹配消息到达时对应定时器会被系统提前取消
                for tid, t_meta in list(active_timers.items()):
                    cond = t_meta.get("condition", "never")
                    if cond == "any" or cond == sender:
                        del active_timers[tid]

    return active_cmd_tasks, active_timers, active_subagents
