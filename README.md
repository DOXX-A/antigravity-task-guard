# Antigravity Task Guard

[English](README.md) | [简体中文](README_zh.md)

---

A deterministic lifecycle hook protection and automated exponential-backoff timer mechanism for terminal background tasks in Google Antigravity (AGY).

---

## Overview

When the agent executes commands using `run_command`, commands exceeding `WaitMsBeforeAsync` automatically degrade into background tasks. In practical workflows—particularly on Windows—this mechanism encounters several recurring failure modes:

1. **Pipe EOF Signal Drops & Zombie Tasks**: On Windows (PowerShell/CMD), toolchains (such as MSVC, MATLAB, Node.js, and Python) often exit without closing inherited standard I/O handles. Antigravity's stream listener waits indefinitely for EOF, leaving the task permanently in a `Running...` state.
2. **Premature Task Degradation**: Conservative timeouts (e.g., 1000–2000ms) needlessly push fast tasks (3–8 seconds) into the background runner.
3. **Uncalibrated Model Timers & 15s Polling Loops**: Models lack real-world time perception and state memory for exponential backoff. They frequently set short fixed timers (e.g., `15s`) and fall into repetitive `15s timer -> status check -> still running -> 15s timer` loops without extending the wait duration.
4. **Spurious Subagent Monitoring**: Antigravity sets `fullyIdle: false` whenever subagents (`invoke_subagent`) are active. Because subagents already have a robust completion and messaging mechanism, treating them as raw terminal tasks forces the parent agent into unnecessary polling loops.

`Antigravity Task Guard` solves these issues at the engine level using deterministic lifecycle hooks—**automating timer durations with exponential backoff, preserving the model's ability to inspect logs and kill hung tasks, and exempting subagents from watchdog monitoring**.

---

## Mechanism

The system operates via four lifecycle hooks configured in [`hooks.json`](hooks.json) backed by a shared transcript state machine in [`scripts/task_state.py`](scripts/task_state.py):

```
[Agent emits run_command]
        │
        ▼
[PreToolUse: pre_run_command.py]
  - Elevates WaitMsBeforeAsync to 10000ms for non-daemon tasks
  - Blocks standalone terminal sleep commands used as makeshift timers
        │
        ├─ (<= 10s) ──> Completes synchronously (never enters background)
        │
        └─ (> 10s)  ──> Degrades to Background Command Task (active_cmd_tasks)
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                           ▼
[PreToolUse: pre_schedule.py]               [Stop: stop_watchdog.py]
  - Subagents only: denies unnecessary timers - Subagents/daemons only: allows stop
  - Uninspected expired timer: requires       - Active cmd without timer: blocks stop
    manage_task(status) before re-scheduling    and guides agent to inspect/schedule
  - Overwrites DurationSeconds via backoff:
    30s -> 60s -> 120s -> 240s -> 480s -> 600s
  - Forces TimerCondition="any" (instant wakeup on completion)
  - Injects structured inspection/kill instructions into Prompt
        │
        ▼
[Timer Expires -> PreToolUse: pre_manage_task.py]
  - Always allows Action="kill" (terminate hung/deadlocked tasks anytime)
  - Allows 1 status check per wakeup cycle to inspect logs
  - Blocks repetitive consecutive status polling loops
```

### 1. Transcript State Machine ([`scripts/task_state.py`](scripts/task_state.py))
Reconstructs the exact lifecycle state of the current turn from `transcript_full.jsonl` / `transcript.jsonl`:
* **`active_cmd_tasks`**: Tracks active non-daemon `run_command` background tasks, their completed watchdog rounds (`schedule_count`), and whether they have been inspected since the last timer expired.
* **`active_timers`**: Tracks active `schedule` timers and their early-cancellation conditions (`TimerCondition`).
* **`active_subagents`**: Tracks `invoke_subagent` instances separately so subagents are never confused with terminal tasks.

### 2. Synchronous Elevation & Anti-Sleep Guard ([`scripts/pre_run_command.py`](scripts/pre_run_command.py))
* Automatically elevates `WaitMsBeforeAsync` to `10000ms` for non-daemon commands so tasks finishing within 10s return synchronously.
* Blocks standalone shell sleep commands (`Start-Sleep`, `sleep`, `timeout /t`) when background tasks or subagents are active.

### 3. Automated Dynamic Backoff Timer ([`scripts/pre_schedule.py`](scripts/pre_schedule.py))
Intercepts one-shot `schedule` calls and takes over timing calculation automatically:
* **Dynamic Exponential Backoff**: Regardless of the `DurationSeconds` passed by the model (e.g., `15s`), the hook overwrites `DurationSeconds` according to the task's retry count:
  $$\text{30s} \rightarrow \text{60s} \rightarrow \text{120s} \rightarrow \text{240s} \rightarrow \text{480s} \rightarrow \text{600s}$$
* **Instant Completion Wakeup**: Overwrites `TimerCondition` to `"any"` so the timer is automatically cancelled the instant the background task finishes.
* **Mandatory Inspection Before Re-Arming**: If a previous timer expired and the model attempts to call `schedule` again without first checking `manage_task(Action="status")`, the hook denies the call until the model inspects the task status and logs.
* **Subagent Exemption**: Denies unnecessary watchdog timers when only subagents are running.

### 4. Task Inspection & Kill Guard ([`scripts/pre_manage_task.py`](scripts/pre_manage_task.py))
* **Preserves Hung-Task Termination**: Unconditionally allows `Action="kill"`, `send_input`, and `list`, and allows `Action="status"` once per cycle so the model can inspect logs and kill deadlocked processes.
* **Prevents Busy-Wait Polling**: Blocks `Action="status"` when a watchdog timer is already ticking or when the model calls `status` repeatedly in the same cycle without waiting.

### 5. Turn Termination Watchdog ([`scripts/stop_watchdog.py`](scripts/stop_watchdog.py))
* Fires when the agent attempts to conclude its turn (`NO_TOOL_CALL`).
* Allows immediate termination if only subagents (`invoke_subagent`) or daemon tasks (`IsDaemon=true`) are running.
* Blocks termination only when an unmonitored `run_command` task is active without a running `schedule` timer.

---

## Directory Structure

```
antigravity-task-guard/
├── hooks.json                  # Hook registration manifest
├── plugin.json                 # Plugin metadata manifest
├── scripts/
│   ├── task_state.py           # Transcript parser & task lifecycle state machine
│   ├── pre_run_command.py      # PreToolUse (run_command) sync elevation & anti-sleep guard
│   ├── pre_schedule.py         # PreToolUse (schedule) automated dynamic backoff & subagent guard
│   ├── pre_manage_task.py      # PreToolUse (manage_task) kill allowance & anti-polling guard
│   └── stop_watchdog.py        # Stop hook watchdog script
├── install.ps1                 # PowerShell installer
├── install.sh                  # Bash installer
├── LICENSE                     # MIT License
├── README.md                   # English documentation
└── README_zh.md                # Chinese documentation
```

---

## Installation

Clone this repository:

```bash
git clone https://github.com/DOXX-A/antigravity-task-guard.git
cd antigravity-task-guard
```

### Option A: Global Installation (Recommended)

Applies across all projects and workspaces on your machine.

**Windows (PowerShell)**:
```powershell
.\install.ps1 -Scope Global
```
Installs to `~/.gemini/config/`.

**Linux / macOS (Bash)**:
```bash
bash install.sh Global
```
Installs to `~/.gemini/config/`.

---

### Option B: Workspace Installation

Applies strictly to the active repository.

**Windows (PowerShell)**:
```powershell
.\install.ps1 -Scope Workspace
```
Installs to `.agents/` in the current working directory.

**Linux / macOS (Bash)**:
```bash
bash install.sh Workspace
```
Installs to `.agents/` in the current working directory.

---

## License

[MIT](LICENSE)
