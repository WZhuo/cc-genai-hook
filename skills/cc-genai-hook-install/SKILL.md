---
name: cc-genai-hook-install
description: |
  安装、配置和诊断 cc-genai-hook：通过 Claude Code command hooks 读取 transcript，
  生成 gen_ai 语义的 invoke_agent / chat / execute_tool / subagent spans，并导出到
  任意 OTLP HTTP/protobuf 后端。
  触发词：cc-genai-hook、claude code trace、claude code 可观测、agent observability、
  otel、gen_ai、tool span、subagent trace、trace 导出。
---

# cc-genai-hook 安装与诊断

> **给执行本 Skill 的 AI agent：**
> 你正在安装和诊断 **cc-genai-hook**。它只处理 **Claude Code**，通过 command hooks
> 读取 transcript，自己生成 OTel spans，再发送到用户指定的 OTLP HTTP/protobuf 后端。
>
> 按步骤执行。遇到 **ASK THE USER** 必须停下来询问，不要猜测 endpoint 或 header。
> **不要回显 API key 或其他 secret。** 安装器会把 header 写入 `0600` 配置文件。

---

## Step 0 — 适用范围

cc-genai-hook 支持：

- Claude Code CLI / IDE / Desktop 会话
- 任意 OTLP HTTP/protobuf 后端
- 任意 header（例如 `x-api-key`、`Authorization`、`x-tenant-id`）

不支持：

- Hermes / OpenClaw / Opencode
- gRPC OTLP（需要先转换为 HTTP/protobuf）

---

## Step 1 — 检查环境

```bash
test -d "$HOME/.claude" && echo "CLAUDE_CODE=yes"
claude --version 2>/dev/null || true
python3 --version
```

要求：

- Claude Code 已安装，支持 command hooks；建议 `2.1.300+`
- Python `3.9+`（推荐 `3.11/3.12`）
- 当前用户可写 `~/.claude/settings.json`

如果 `~/.claude` 不存在，**ASK THE USER** 确认 Claude Code 是否已安装。

---

## Step 2 — 获取 OTLP endpoint 和 headers

需要用户提供：

1. **Endpoint**
   - 目标后端的 OTLP HTTP endpoint
   - 通常是 base URL，例如 `https://otel.example.com`
   - cc-genai-hook 会拼接 `/v1/traces`
   - 如果用户给的是完整 `/v1/traces` URL，安装时加 `--endpoint-kind full`

2. **Headers**
   - 后端要求的认证 header
   - 例如：
     - `x-api-key: <key>`
     - `Authorization: Bearer <token>`
     - `x-tenant-id: <tenant>`
   - 可以有多个 header

### 2a. 检查已有配置

```bash
test -f "$HOME/.claude/genai-hook.json" && \
  python3 -c "
import json, os
p=os.path.expanduser('~/.claude/genai-hook.json')
c=json.load(open(p))
e=c.get('exporter',{})
print('endpoint:', e.get('endpoint'))
print('headers:', list((e.get('headers') or {}).keys()))
"
```

如果已有配置，**ASK THE USER**：

> 我找到了已有的 cc-genai-hook 配置。要复用它，还是更新为新的 endpoint / header？

不要打印 header 的值。

### 2b. 如果没有配置

**ASK THE USER**：

> 我需要目标 OTLP 后端的 **Endpoint** 和需要的 **header**（名称和值）。
> 例如 `x-api-key=...` 或 `Authorization=Bearer ...`。
> 请把 endpoint 和 header 发给我；我不会回显 secret。

等待用户提供，不要编造。

### 2c. 校验

- Endpoint 必须是 `http://` 或 `https://`
- 如果 endpoint 以 `/v1/traces` 结尾，使用 `--endpoint-kind full`
- Header 非空

保存为 shell 变量：

```bash
ENDPOINT="<user endpoint>"
API_KEY="<user key>"
```

---

## Step 3 — 安装 cc-genai-hook

### 3a. 检查 CLI

```bash
command -v cc-genai-hook && cc-genai-hook --version
```

如果已有可用版本：

```bash
CCG="$(command -v cc-genai-hook)"
```

如果没有，安装到稳定目录。**不要**把 venv 放到临时目录，因为 hook 命令会引用它。

```bash
CCGH_HOME="$HOME/.local/share/cc-genai-hook"
mkdir -p "$CCGH_HOME"

if [ -d "$CCGH_HOME/src/.git" ]; then
  git -C "$CCGH_HOME/src" pull --ff-only
else
  git clone https://github.com/clickzetta/cc-genai-hook.git "$CCGH_HOME/src"
fi

python3 -m venv "$CCGH_HOME/venv"
"$CCGH_HOME/venv/bin/pip" install -U pip setuptools wheel
"$CCGH_HOME/venv/bin/pip" install "$CCGH_HOME/src"
CCG="$CCGH_HOME/venv/bin/cc-genai-hook"
"$CCG" --version
```

### 3b. 安装 hooks

默认使用 **ordered 模式**：Stop 时统一生成 chat / execute_tool / subagent，保证顺序。

```bash
"$CCG" install \
  --mode command \
  --endpoint "$ENDPOINT" \
  --header "x-api-key=$API_KEY" \
  --user "$(whoami)"
```

如果 endpoint 已经是完整 `/v1/traces`：

```bash
"$CCG" install \
  --mode command \
  --endpoint "$ENDPOINT" \
  --endpoint-kind full \
  --header "x-api-key=$API_KEY" \
  --user "$(whoami)"
```

多个 header：

```bash
"$CCG" install \
  --mode command \
  --endpoint "$ENDPOINT" \
  --header "x-api-key=$API_KEY" \
  --header "x-tenant-id=$TENANT_ID" \
  --user "$(whoami)"
```

### 3c. 可选：realtime 模式

如果希望工具 span 在工具结束后立即发送：

```bash
"$CCG" install \
  --mode command \
  --tool-mode realtime \
  --endpoint "$ENDPOINT" \
  --header "x-api-key=$API_KEY" \
  --user "$(whoami)"
```

- `ordered`（默认）：Stop 时统一发送，顺序严格
- `realtime`：额外注册 PostToolUse / PostToolUseFailure，工具 span 实时发送
- realtime 模式不展开 subagent 子树

### 3d. 安装器做了什么

- 写入 `~/.claude/genai-hook.json`（`0600`）
- 备份并合并 `~/.claude/settings.json`
- 默认添加 hooks：
  - `Stop`
  - `SessionEnd`
- realtime 模式再加：
  - `PostToolUse`
  - `PostToolUseFailure`
- **不会**写入 Claude Code 原生 telemetry 变量：
  - `CLAUDE_CODE_ENABLE_TELEMETRY`
  - `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`
  - `OTEL_TRACES_EXPORTER`
  - `OTEL_LOG_*`

---

## Step 4 — 验证配置

```bash
"$CCG" doctor
"$CCG" doctor --send-test
"$CCG" config show
```

预期：

- `endpoint` 是 `$ENDPOINT/v1/traces`
- `protocol` 是 `http/protobuf`
- `headers` 只显示名字
- `hooks` 至少包含 `Stop` 和 `SessionEnd`
- `doctor --send-test` 输出 `test span sent`

如果失败：

1. 检查 endpoint 是否可达
2. 检查 header 名和值
3. 检查是否误带 `/v1/traces`
4. 检查是否需要 `--endpoint-kind full`
5. 检查代理 / 防火墙 / DNS

本地 dry-run：

```bash
CC_GENAI_HOOK_DRY_RUN=1 claude -p "hello"
cat "$HOME/.claude/state/genai-hook/dry-run.jsonl"
```

dry-run 只写本地文件，不发送。

---

## Step 5 — Trace 结构

```text
invoke_agent（turn root）
├── chat
├── execute_tool
└── execute_tool（Agent/Task delegation）
    └── subagent（container）
        ├── chat
        ├── execute_tool
        └── subagent response
```

关键属性：

- `gen_ai.operation.name`
- `gen_ai.request.model` / `gen_ai.response.model`
- `gen_ai.usage.*`
- `gen_ai.conversation.id`
- `gen_ai.tool.name` / `gen_ai.tool.call.id`
- `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result`
- `gen_ai.input.messages` / `gen_ai.output.messages`
- `gen_ai.agent.id` / `gen_ai.agent.name`（subagent）
- `claude_code.genai_hook.step_index` / `step_name` / `plan_step` / `step_count`

---

## Step 6 — 常见问题

### Q1. `cc-genai-hook: command not found`

使用绝对路径：

```bash
"$HOME/.local/share/cc-genai-hook/venv/bin/cc-genai-hook" doctor
```

### Q2. Endpoint 404

- base endpoint 不要带 `/v1/traces`
- 完整路径使用 `--endpoint-kind full`
- 不要根据 Web UI 域名猜 collector 域名

### Q3. 401 / 403

- header 名或值错误
- API key 类型不对
- 检查后端是否需要 `Authorization` 而不是 `x-api-key`

### Q4. 没有 subagent 子树

- 确认 `tool_mode=ordered`
- 确认 Agent/Task tool result 里有 `toolUseResult.agentId`
- 确认 `subagents/agent-<id>.jsonl` 存在
- realtime 模式不展开 subagent

### Q5. 内容太多 / 敏感信息

编辑 `~/.claude/genai-hook.json`：

```json
{
  "content": {
    "mode": "last_turn",
    "include_thinking": false,
    "redact_patterns": ["(?i)bearer\\s+\\S+"]
  }
}
```

- `mode=none`：只发结构
- `mode=last_turn`：只发当前 turn
- 修改后新 hook 进程会读取配置

### Q6. Python / pip 依赖

使用稳定 venv：

```bash
CCGH_HOME="$HOME/.local/share/cc-genai-hook"
mkdir -p "$CCGH_HOME"
git clone https://github.com/clickzetta/cc-genai-hook.git "$CCGH_HOME/src"
python3 -m venv "$CCGH_HOME/venv"
"$CCGH_HOME/venv/bin/pip" install -U pip setuptools wheel
"$CCGH_HOME/venv/bin/pip" install "$CCGH_HOME/src"
```

### Q7. `--mode sidecar` 报错

sidecar 尚未实现（计划 M4）。当前只支持：

```bash
--mode command
```

---

## Step 7 — 升级 / 卸载

升级：

```bash
CCGH_HOME="$HOME/.local/share/cc-genai-hook"
git -C "$CCGH_HOME/src" pull --ff-only
"$CCGH_HOME/venv/bin/pip" install -U "$CCGH_HOME/src"
"$CCG" install --mode command \
  --endpoint "$ENDPOINT" \
  --header "x-api-key=$API_KEY" \
  --user "$(whoami)"
```

卸载 hooks：

```bash
"$CCG" uninstall
```

同时删除配置和 state：

```bash
"$CCG" uninstall --purge
```

---

## Step 8 — 汇报变更

向用户汇报：

- endpoint 域名
- tool_mode：`ordered` / `realtime`
- 写入文件：
  - `~/.claude/genai-hook.json`
  - `~/.claude/settings.json`
- 安装的 hooks
- `doctor` / `doctor --send-test` 结果
- 提醒用户查看后端 Trace Explorer

---

## 不要做的事

- 不要回显 API key 或其他 secret
- 不要使用 gRPC OTLP
- 不要写入 Claude Code 原生 telemetry 变量
- 不要在没有备份的情况下覆盖 `~/.claude/settings.json`
- 不要把 `--mode sidecar` 当成已实现功能
- 不要在没有 `toolUseResult.agentId` 时强行构造 subagent 子树
