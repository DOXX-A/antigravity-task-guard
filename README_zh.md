# Antigravity Task Guard

[English](README.md) | [简体中文](README_zh.md)

---

适用于 Google Antigravity (AGY) 的后台终端任务生命周期守护与自动化退避计时机制，用于解决长时间任务挂起、管道死锁、模型短时轮询死循环及子代理误拦截问题。

---

## 问题背景

在 Antigravity 交互中，终端工具（`run_command`）在耗时超过 `WaitMsBeforeAsync` 时会自动降级转入后台任务（Background Task）。在实际开发中，该机制常面临以下几类典型问题：

1. **管道 EOF 信号丢失与假死**：在 Windows PowerShell / CMD 环境下，部分构建或仿真工具（如 MSVC、MATLAB、Node.js、Python）在派生子进程或未完全关闭标准流句柄时退出，导致父级流监听器无法捕获 EOF，终端状态永久卡在 `Running...`。
2. **过早降级**：默认或较短的等待时间（如 1000~2000ms）导致原本 3~8 秒即可同步完成的命令频繁转入后台，增加异步管理复杂度。
3. **模型计时失控与短周期轮询死循环**：模型对真实物理耗时缺乏感知，常设定极短的定时器（如 `15s`），且缺乏指数退避（Exponential Backoff）记忆，陷入 `15s 定时 -> status 检查 -> 未完成 -> 15s 定时` 的高频轮询死循环，大量消耗上下文与配额。
4. **子代理（Subagent）误监控**：系统底层的 `fullyIdle` 状态将子代理（`invoke_subagent`）与终端后台任务混为一谈。子代理本身具备健壮的退出与消息通知机制，若被当作普通终端任务强制要求挂载定时器，会导致主代理无意义地反复轮询子代理。

`Antigravity Task Guard` 通过系统底层的生命周期钩子（Lifecycle Hooks）构建确定性的闭环状态机，**将计时与动态退避完全交给自动化接管，同时保留模型检查任务日志与杀死假死任务的能力，并自动豁免子代理监控**。

---

## 机制设计

本项目通过配置在 [`hooks.json`](hooks.json) 中的四个生命周期钩子与状态机引擎 [`scripts/task_state.py`](scripts/task_state.py) 协同工作：

```
[Agent 发起 run_command]
        │
        ▼
[PreToolUse: pre_run_command.py]
  - 非守护任务自动提升 WaitMsBeforeAsync 至 10000ms
  - 拦截用终端 sleep 充当轮询定时器的反模式行为
        │
        ├─ (<= 10s) ──> 同步直接返回，不进后台
        │
        └─ (> 10s)  ──> 降级为后台命令任务 (active_cmd_tasks)
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼                                           ▼
[PreToolUse: pre_schedule.py]               [Stop: stop_watchdog.py]
  - 仅子代理运行时：拒绝无意义定时轮询        - 仅子代理/守护进程运行时：直接放行休眠
  - 定时器到期未检查时：强制先调 status       - 存在活动命令且无定时器：拦截并指引
  - 自动按指数退避改写 DurationSeconds:
    30s -> 60s -> 120s -> 240s -> 480s -> 600s
  - 强制注入 TimerCondition="any" (完成即秒级唤醒)
  - 自动注入带轮次与排查指引的唤醒 Prompt
        │
        ▼
[定时器到期唤醒 -> PreToolUse: pre_manage_task.py]
  - 无条件放行 Action="kill" (随时杀死假死/死锁任务)
  - 允许每轮到期后调用 1 次 status 检查任务日志与进展
  - 拦截同一周期内连续原地轮询 status 的死循环行为
```

### 1. 状态机还原引擎（[`scripts/task_state.py`](scripts/task_state.py)）
实时解析会话轨迹日志（`transcript_full.jsonl` / `transcript.jsonl`），精确区分：
* **活动终端任务（`active_cmd_tasks`）**：仅追踪非守护进程（`IsDaemon != true`）的 `run_command` 后台任务及其已历经的定时轮次（`schedule_count`）与到期检查状态。
* **活动看门狗定时器（`active_timers`）**：追踪当前正在倒计时的 `schedule` 任务及提前取消条件（`TimerCondition`）。
* **活动子代理（`active_subagents`）**：独立识别 `invoke_subagent` 子代理生命周期，与终端命令任务解耦。

### 2. 前置同步提速与反轮询（[`scripts/pre_run_command.py`](scripts/pre_run_command.py)）
* 拦截所有 `run_command` 调用，对非守护进程自动将 `WaitMsBeforeAsync` 提升至系统上限（`10000ms`），源头消除 80% 以上的短时后台任务。
* 拦截后台已有任务时模型试图通过 `Start-Sleep` / `sleep` / `timeout /t` 在终端内原地阻塞等待的反模式调用。

### 3. 自动化指数退避计时（[`scripts/pre_schedule.py`](scripts/pre_schedule.py)）
拦截所有一次性 `schedule` 调用，彻底接管模型的计时逻辑：
* **动态退避时长改写**：无论模型传入 `5s` 还是 `15s`，Hook 根据当前后台任务已等待的轮次，通过 `overwrite` 自动按指数退避阶梯改写 `DurationSeconds`：
  $$\text{30s} \rightarrow \text{60s} \rightarrow \text{120s} \rightarrow \text{240s} \rightarrow \text{480s} \rightarrow \text{600s}$$
* **任务完成秒级唤醒**：强制设置 `TimerCondition: "any"`。一旦后台任务在计时期间正常完成，系统原生事件通知会立即唤醒模型并自动销毁定时器，绝不额外空等。
* **强制先检查后续期**：当上一轮定时器到期唤醒模型后，若模型未调用 `manage_task(Action="status")` 检查任务日志就盲目再次调用 `schedule`，Hook 将予以拦截，确保假死任务能被及时发现并终止。
* **子代理免监控**：当后台仅有子代理运行而无 `run_command` 任务时，直接拒绝多余的 `schedule` 调用，引导模型直接休眠等待子代理通知。

### 4. 任务检查与假死清理守卫（[`scripts/pre_manage_task.py`](scripts/pre_manage_task.py)）
* **保留完整查杀能力**：无条件放行 `Action="kill"`、`send_input` 与 `list`，且在每轮定时器到期后允许模型调用 `Action="status"` 查看任务最新日志。
* **阻断原地死循环轮询**：阻止模型在已挂载活动定时器后继续调用 `status`，或在同一唤醒周期内对同一运行中任务连续多次调用 `status`。

### 5. 终局休眠守卫（[`scripts/stop_watchdog.py`](scripts/stop_watchdog.py)）
* 在模型准备结束本轮回复（`NO_TOOL_CALL`）时触发。
* 若后台仅有子代理（`invoke_subagent`）或常驻守护进程（`IsDaemon=true`），直接静默放行休眠。
* 若存在未挂载定时器的活动 `run_command` 任务，则拦截停止请求，并根据当前所处状态引导模型检查/杀死假死任务或调用 `schedule` 启动下一轮自动退避看门狗。

---

## 目录结构

```
antigravity-task-guard/
├── hooks.json                  # 钩子注册清单
├── plugin.json                 # 插件元数据清单
├── scripts/
│   ├── task_state.py           # 会话轨迹解析与后台任务状态机核心模块
│   ├── pre_run_command.py      # PreToolUse (run_command) 同步提速与反 sleep 轮询脚本
│   ├── pre_schedule.py         # PreToolUse (schedule) 自动指数退避计时与子代理豁免脚本
│   ├── pre_manage_task.py      # PreToolUse (manage_task) 假死查杀放行与防连续轮询脚本
│   └── stop_watchdog.py        # Stop 终局休眠守卫脚本
├── install.ps1                 # Windows (PowerShell) 安装脚本
├── install.sh                  # Linux / macOS (Bash) 安装脚本
├── LICENSE                     # MIT 协议
├── README.md                   # 英文说明文档
└── README_zh.md                # 中文说明文档
```

---

## 安装使用

首先克隆本项目到本地：

```bash
git clone https://github.com/DOXX-A/antigravity-task-guard.git
cd antigravity-task-guard
```

### 方式 A：全局安装（推荐）

全局安装将对当前机器上的所有工作区与项目统一生效。

**Windows (PowerShell)**:
```powershell
.\install.ps1 -Scope Global
```
安装目标路径为 `~/.gemini/config/`。

**Linux / macOS (Bash)**:
```bash
bash install.sh Global
```
安装目标路径为 `~/.gemini/config/`。

---

### 方式 B：单工作区安装

仅在当前项目生效，便于随代码仓库分发。

**Windows (PowerShell)**:
```powershell
.\install.ps1 -Scope Workspace
```
安装目标路径为当前目录下的 `.agents/`。

**Linux / macOS (Bash)**:
```bash
bash install.sh Workspace
```
安装目标路径为当前目录下的 `.agents/`。

---

## License

[MIT](LICENSE)
