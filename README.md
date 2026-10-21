# Antigravity Task Guard

适用于 Google Antigravity (AGY) 的后台终端任务生命周期守护机制，用于解决长时间任务挂起、管道死锁及会话永久假死问题。

---

## 问题背景

在 Antigravity 交互中，终端工具（`run_command`）在耗时超过 `WaitMsBeforeAsync` 时会降级转入后台任务（Background Task）。在实际开发（尤其是 Windows 环境）中，该机制常面临以下几种挂起场景：

1. **管道 EOF 信号丢失**：在 Windows PowerShell / cmd 环境下，部分构建工具（如 MSVC、Node、Python）在派生子进程或未完全关闭标准流句柄时退出，导致父级流监听器无法捕获 EOF，终端状态被永久置为 `Running...`。
2. **交互式输入阻塞**：命令未携带非交互参数（如 `-y` / `--no-input`）时等待 stdin 输入，后台任务无法交互从而挂起。
3. **过早降级**：默认或较短的等待时间（如 1000~2000ms）导致原本 3~5 秒即可同步完成的命令频繁转入后台，增加了异步通知失败的风险。
4. **Prompt 软约束失效**：单纯依靠在 `GEMINI.md` 中要求 Agent“转入后台后必须设置看门狗”属于软约束。随着对话轮次增加和上下文膨胀，模型存在概率性漏调 `schedule` 的情况，导致会话失去唤醒锚点。

---

## 机制设计

本项目采用**生命周期钩子（Lifecycle Hooks）硬拦截**与**提示词规程（SOP）闭环**结合的双层防护策略：

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
                     - 倒序检索上下文轨迹中的 schedule 记录
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
      [已设定时器]                      [未设定时器]
            │                                 │
            ▼                                 ▼
        静默放行休眠                  强行拦截并驳回休眠
                                      注入系统警告迫使 Agent 补调
```

### 1. 前置优化（PreToolUse）
* **文件**：`scripts/pre_run_command.py`
* **行为**：拦截 `run_command`。对非守护进程（`IsDaemon != true`），若等待时间小于系统上限（10000ms），通过 `overwrite` 自动提升至 10000ms。使日常编译、检索和常规测试均在同步阶段完成，大幅减少后台任务生成。

### 2. 终局拦截（Stop）
* **文件**：`scripts/stop_watchdog.py`
* **行为**：在 Agent 尝试停止输出时触发。若 `fullyIdle == false` 且当前轮次未检测到调用 `schedule` 定时器，则返回 `decision: "continue"` 驳回停止请求，并向上下文注入告警，强制 Agent 调用 `schedule` 设置超时保护。

### 3. 规程闭环（GEMINI SOP）
* **文件**：`rules/GEMINI_RULE.md`
* **行为**：定义看门狗唤醒后的标准作业程序。Agent 被唤醒后必须检查日志增量；若无更新或异常超时，强制执行 `kill` 终止任务，禁止盲目无限制续订定时器。

---

## 目录结构

```
antigravity-task-guard/
├── hooks.json                     # Antigravity 生命周期钩子配置
├── scripts/
│   ├── pre_run_command.py         # PreToolUse 钩子执行脚本
│   └── stop_watchdog.py          # Stop 钩子执行脚本
├── rules/
│   └── GEMINI_RULE.md             # 推荐配置到 GEMINI.md 的 SOP 规则
├── install.ps1                    # Windows (PowerShell) 安装脚本
├── install.sh                     # Linux / macOS (Bash) 安装脚本
├── LICENSE                        # MIT 协议
└── README.md
```

---

## 安装使用

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

## 配置规程

安装脚本文件后，需将 [rules/GEMINI_RULE.md](rules/GEMINI_RULE.md) 中的规则追加至您的全局规程（如 `~/.gemini/GEMINI.md`）或项目规程中：

```markdown
## 后台任务防御与看门狗处置 SOP

1. **源头防护与同步优先**：
   - 任何命令必须优先携带非交互标志（如 `-y`、`--non-interactive`、`--no-input`、`--batch`），严禁触发控制台交互等待。
   - 凡执行编译、测试、安装等可能耗时的非守护任务，优先留足同步等待时间（系统已配置 `PreToolUse` Hook 自动提速至 10000ms），尽量在同步阶段完成，避免无谓掉入后台。
   - 原生原语优先：能用 `view_file`、`write_to_file`、`replace_file_content` 解决的，严禁使用 shell 执行文件查看或写入。

2. **降级看门狗与 Hook 双重闭环**：
   - 任何命令一旦降级转入后台任务（Background Task），在结束本轮回复前，必须同步调用 `schedule` 设置一个合理时间的看门狗定时器（建议 60~120 秒）。
   - 若遗漏设置，系统全局 `Stop` Hook 将强制拦截并驳回挂起，直至设置看门狗定时器。

3. **看门狗唤醒处置闭环（严禁盲目续约）**：
   - 当定时器唤醒 Agent 时，第一步必须调用 `manage_task(Action='status')` 检查最新日志。
   - 若日志已停止更新或任务无响应，必须果断调用 `manage_task(Action='kill')` 或 `taskkill` 终止任务，严禁在无明确增量进度的情况下无限制二次续约定时器。
   - 终止后明确向用户汇报终端最后输出与排查建议。
```

---

## 验证与测试

可以通过以下两步验证机制是否正常加载：

1. **同步提速验证**：
   执行任意耗时约 3~5 秒的命令，故意传入较短的 `WaitMsBeforeAsync`（如 2000ms）：
   ```powershell
   powershell -Command "Start-Sleep -Seconds 4; Write-Output 'Done'"
   ```
   若终端提示 `A pre-tool hook changed the arguments of this tool call before it ran. Changed: WaitMsBeforeAsync.` 且命令直接同步返回，表明 `PreToolUse` 钩子生效。

2. **拦截验证**：
   发起一个耗时超过 10 秒的后台命令，且不调用 `schedule` 定时器直接尝试结束回复：
   ```powershell
   powershell -Command "Start-Sleep -Seconds 20; Write-Output 'Done'"
   ```
   系统应触发并输出 `Stop hook blocked termination: 【Hook 看门狗安全拦截】...`，表明 `Stop` 钩子生效。

---

## License

[MIT](LICENSE)
