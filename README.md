# Antigravity Task Guard

[English](README.md) | [简体中文](README_zh.md)

---

A deterministic lifecycle hook protection mechanism for terminal background tasks in Google Antigravity (AGY).

---

## Overview

When the agent executes commands using `run_command`, commands exceeding `WaitMsBeforeAsync` automatically degrade into background tasks. In practical workflows—particularly on Windows—this mechanism frequently encounters silent hangs:

* **Pipe EOF Signal Drops**: On Windows (PowerShell/CMD), toolchains (such as MSVC, Node.js, and Python) often exit without closing inherited standard I/O handles. Antigravity's stream listener waits indefinitely for EOF, leaving the task permanently in a `Running...` state.
* **Premature Task Degradation**: Conservative timeouts (e.g. 1000–2000ms) needlessly push fast tasks (3–5 seconds) into the background runner, increasing async notification failure risks.
* **Prompt Rule Fragility**: Natural language rules (such as instructions in custom prompt files) are soft constraints. As conversation history grows, models probabilistically omit watchdog timers, leaving hanging background tasks unmonitored.

`Antigravity Task Guard` replaces prompt reliance with deterministic system hooks, preventing hangs at the engine level without requiring custom system prompt modifications.

---

## Mechanism

The system operates via two Antigravity lifecycle hooks configured in `hooks.json`:

```
[Agent emits run_command]
          │
          ▼
[PreToolUse: pre_run_command.py]
  - Inspects WaitMsBeforeAsync
  - Elevates timeout to 10000ms for non-daemon tasks
          │
          ├─ (<= 10s) ──> Completes synchronously (never enters background)
          │
          └─ (> 10s)  ──> Degrades to Background Task
                                │
                                ▼
                      [Agent attempts to finish turn]
                                │
                                ▼
                      [Stop Hook: stop_watchdog.py]
                        - Inspects fullyIdle state
                        - Checks turn transcript for schedule tool calls
                                │
               ┌────────────────┴────────────────┐
               ▼                                 ▼
        [Watchdog Active]              [No Watchdog Set]
               │                                 │
               ▼                                 ▼
        Allow Turn Stop                Block Termination
                                       Force Agent to Set Watchdog
```

### 1. PreToolUse (`scripts/pre_run_command.py`)
Intercepts all `run_command` invocations. For non-daemon tasks (`IsDaemon != true`), it automatically overwrites `WaitMsBeforeAsync` to the platform maximum (10000ms). Tasks completing within 10 seconds return synchronously, eliminating the majority of spurious background task transitions.

### 2. Stop Hook (`scripts/stop_watchdog.py`)
Fires when the agent attempts to conclude its turn (`NO_TOOL_CALL`). If background tasks remain active (`fullyIdle == false`) and no `schedule` watchdog was registered in the current turn, the hook blocks termination (`decision: "continue"`). It injects a warning into the agent context, compelling it to inspect the task status or register a timeout watchdog.

---

## Directory Structure

```
antigravity-task-guard/
├── hooks.json                 # Hook registration manifest
├── scripts/
│   ├── pre_run_command.py     # PreToolUse execution script
│   └── stop_watchdog.py      # Stop hook execution script
├── install.ps1                # PowerShell installer
├── install.sh                 # Bash installer
├── LICENSE                    # MIT License
├── README.md                  # English documentation
└── README_zh.md               # Chinese documentation
```

---

## Installation

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

## Verification

You can verify both hooks in an active Antigravity session:

### 1. Verify Synchronous Elevation (PreToolUse)
Run a command lasting ~4 seconds with an intentionally small wait threshold:
```powershell
powershell -Command "Start-Sleep -Seconds 4; Write-Output 'Done'"
```
The console will display:
```text
A pre-tool hook changed the arguments of this tool call before it ran. Changed: WaitMsBeforeAsync.
```
The command returns synchronously without generating a background task ID.

### 2. Verify Watchdog Interception (Stop)
Run a task exceeding 10 seconds, then attempt to conclude the turn without setting a `schedule` timer:
```powershell
powershell -Command "Start-Sleep -Seconds 20; Write-Output 'Done'"
```
The Stop hook immediately intercepts termination:
```text
Stop hook blocked termination: [Hook Watchdog Intercept] Active background task(s) detected, but no schedule watchdog timer was set in this turn...
```
The agent is prompted to inspect task status or set a `schedule` watchdog timer before it can finish.

---

## License

[MIT](LICENSE)
