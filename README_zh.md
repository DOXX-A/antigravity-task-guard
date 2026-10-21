# Antigravity Task Guard

[English](README.md) | [简体中文](README_zh.md)

---

适用于 Google Antigravity (AGY) 的后台终端任务生命周期守护机制，用于解决长时间任务挂起、管道死锁及会话永久假死问题。

---

## 问题背景

在 Antigravity 交互中，终端工具（`run_command`）在耗时超过 `WaitMsBeforeAsync` 时会自动降级转入后台任务（Background Task）。在实际开发（尤其是 Windows 环境）中，该机制常面临以下几种挂起场景：

* **管道 EOF 信号丢失**：在 Windows PowerShell / CMD 环境下，部分构建工具（如 MSVC、Node.js、Python）在派生子进程或未完全关闭标准流句柄时退出，导致父级流监听器无法捕获 EOF，终端状态被永久置为 `Running...`。
* **过早降级**：默认或较短的等待时间（如 1000~2000ms）导致原本 3~5 秒即可同步完成的命令频繁转入后台，增加了异步通知失败的风险。
* **Prompt 软约束失效**：单纯依靠在提示词（如 `GEMINI.md`）中要求 Agent“转入后台后必须设置看门狗”属于软约束。随着对话轮次增加和上下文膨胀，模型存在概率性漏调 `schedule` 的情况，导致会话失去唤醒锚点。

`Antigravity Task Guard` 通过系统底层的生命周期钩子（Lifecycle Hooks）进行硬拦截，无需修改任何提示词规则文件，从引擎层面杜绝后台假死。

---

## 机制设计

本项目由两个配置在 `hooks.json` 中的生命周期钩子协同运作：

```
[Agent 发起 run_command]
       │
       ▼
[PreToolUse 钩子: pre_run_command.py]
  - 检查 WaitMsBeforeAsync
  - 非守护任务自动提升至 10000ms (源头规避 80% 假性后台任务)
       │
       ├─ (<= 10s) ──> 同步直接返回，不进后台
       │
       └─ (> 10s)  ──> 降级为 Background Task
                             │
                             ▼
                   [Agent 准备结束本轮回复]
                             │
                             ▼
                   [Stop 钩子: stop_watchdog.py]
                     - 检查 fullyIdle 状态
                     - 检索当前轮次中的 schedule 调用记录
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
      [已设定时器]                      [未设定时器]
            │                                 │
            ▼                                 ▼
        静默放行休眠                  强行拦截并驳回休眠
                                      注入系统警告迫使 Agent 检查或补调
```

### 1. 前置优化（PreToolUse: `scripts/pre_run_command.py`）
拦截所有 `run_command` 调用。对非守护进程（`IsDaemon != true`），若等待时间小于系统上限（10000ms），通过 `overwrite` 自动提升至 10000ms。使日常编译、检索和常规测试均在同步阶段完成，大幅减少后台任务生成。

### 2. 终局拦截（Stop: `scripts/stop_watchdog.py`）
在 Agent 尝试停止输出（`NO_TOOL_CALL`）时触发。若后台仍有未完成任务（`fullyIdle == false`）且当前轮次未检测到调用 `schedule` 定时器，则返回 `decision: "continue"` 驳回停止请求，并向上下文注入告警，强制 Agent 检查任务状态或设置超时看门狗。

---

## 目录结构

```
antigravity-task-guard/
├── hooks.json                 # 钩子注册清单
├── scripts/
│   ├── pre_run_command.py     # PreToolUse 钩子执行脚本
│   └── stop_watchdog.py      # Stop 钩子执行脚本
├── install.ps1                # Windows (PowerShell) 安装脚本
├── install.sh                 # Linux / macOS (Bash) 安装脚本
├── LICENSE                    # MIT 协议
├── README.md                  # 英文说明文档
└── README_zh.md               # 中文说明文档
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

## 验证与测试

可以通过以下两步验证机制是否正常加载：

### 1. 同步提速验证（PreToolUse）
执行耗时约 4 秒的命令，传入较短的等待时间（如 2000ms）：
```powershell
powershell -Command "Start-Sleep -Seconds 4; Write-Output 'Done'"
```
控制台将输出：
```text
A pre-tool hook changed the arguments of this tool call before it ran. Changed: WaitMsBeforeAsync.
```
命令将在同步阶段直接返回，不会产生后台任务 ID。

### 2. 拦截验证（Stop）
发起一个耗时超过 10 秒的后台命令，且不调用 `schedule` 定时器直接尝试结束回复：
```powershell
powershell -Command "Start-Sleep -Seconds 20; Write-Output 'Done'"
```
系统底层的 Stop 钩子将立即拦截终止：
```text
Stop hook blocked termination: [Hook Watchdog Intercept] Active background task(s) detected, but no schedule watchdog timer was set in this turn...
```
Agent 将被系统强制唤醒，要求检查任务状态或补齐看门狗定时器。

---

## License

[MIT](LICENSE)
