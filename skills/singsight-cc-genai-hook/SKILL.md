---
name: singsight-cc-genai-hook
description: |
  使用 cc-genai-hook 将 Claude Code 的 trace 接入 Singsight（或任意 OTLP HTTP 后端）。
  通过 Claude Code command hooks 读取 transcript，生成 gen_ai 语义的 invoke_agent / chat /
  execute_tool / subagent spans，再通过 OTLP 发送到 Singsight。
  触发词：singsight、接入 singsight、claude code 可观测、agent trace、otel、gen_ai、
  cc-genai-hook、token usage、trace explorer。
  不用于 Hermes / OpenClaw / Opencode；本项目只处理 Claude Code。
---

# 使用 cc-genai-hook 将 Claude Code trace 接入 Singsight

> **给执行本 Skill 的 AI agent：**
> 你正在指导用户把 **Claude Code** 的 trace 接入 **Singsight**。
> 本项目使用 **cc-genai-hook + Claude Code command hooks**，不依赖 Claude Code 原生
> OTel trace，也不使用 `CLAUDE_CODE_ENABLE_TELEMETRY` / `OTEL_TRACES_EXPORTER` 那一套。
>
> 按步骤执行。遇到 **ASK THE USER** 必须停下来询问，不要猜测 endpoint 或 API key。
> **永远不要把 API key 回显给用户。** 安装器会把 key 写入 `0600` 配置文件。

---

## Step 0 — 适用范围

本项目只处理 **Claude Code**。

- ✅ Claude Code CLI / IDE / Desktop 会话
- ❌ Hermes Agent、OpenClaw、Opencode（不要套用本 Skill）

如果用户明确要接入其他 agent，告诉用户本 Skill 只覆盖 Claude Code。

---

## Step 1 — 确认 Claude Code 环境

检查：

```bash
test -d "$HOME/.claude" && echo "CLAUDE_CODE=yes"
claude --version 2>/dev/null || true
python3 --version
```

要求：

- Claude Code 已安装，支持 command hooks；建议较新版本（`2.1.300+`）。
- Python `3.9+`（推荐 `3.11/3.12`）。
- 当前用户可写 `~/.claude/settings.json`。

如果 `~/.claude` 不存在，**ASK THE USER** 确认是否已经安装 Claude Code。

---

## Step 2 — 获取 Singsight Endpoint 和 API Key

> **Endpoint 格式**：保存 **base URL**，例如 `https://collector.example.com`。
> 不要带 `/v1/traces`，不要带尾部 `/`。cc-genai-hook 会自己拼接 `/v1/traces`。
>
> **协议**：Singsight 只支持 OTLP **http/protobuf**。不要使用 gRPC（`:4317`）。

需要两个值：

1. **Endpoint**
   - 打开 Singsight Web UI
   - **Settings → OTel Endpoint** 或 **Settings → Quick Start**
   - 复制当前项目显示的 collector endpoint
   - 不要根据 Web UI 域名猜测 endpoint

2. **API Key**
   - **Settings → API Keys → Ingest Keys**
   - 点击 **Create Ingest Key**
   - 完整 key 只显示一次，立即复制
   - 必须是 **Ingest** 类型；Access Key 不能用于 ingestion
   - header 名固定为 `x-singsight-apikey`

### 2a. 先检查是否已经配置过

优先检查本项目的独立配置：

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

> 我在 `~/.claude/genai-hook.json` 找到了已有的 cc-genai-hook 配置。
> 要复用同一个 endpoint / API key，还是更新为新的值？

- 复用：把已有 endpoint 和 key 读入 `$ENDPOINT` / `$API_KEY`，继续。
- 更新：让用户提供新值。

不要打印 key 的值。

### 2b. 如果没有配置

**ASK THE USER**：

> 我需要你的 Singsight **Endpoint** 和 **API Key** 才能完成接入。
>
> 请打开 Singsight Web UI：
> 1. **Settings → OTel Endpoint** / **Settings → Quick Start**：复制 Endpoint
> 2. **Settings → API Keys → Ingest Keys**：创建 Ingest Key，并复制完整 key
>
> 请把两个值发给我。我不会回显 API key。

等待用户提供，不要编造。

### 2c. 校验

- Endpoint 必须以 `http://` 或 `https://` 开头。
- Endpoint 不能带 `/v1/traces`（除非使用 `--endpoint-kind full`，见 Step 3）。
- API Key 非空。

保存为 shell 变量：

```bash
ENDPOINT="<用户提供的 endpoint>"
API_KEY="<用户提供的 key>"
```

---

## Step 3 — 安装 cc-genai-hook

### 3a. 检查 CLI 是否已安装

```bash
command -v cc-genai-hook && cc-genai-hook --version
```

如果已有可用版本，设置：

```bash
CCG="$(command -v cc-genai-hook)"
```

如果没有，安装到稳定目录。**不要**把 venv 放到临时目录，因为 hook 命令会引用它。

```bash
git clone https://github.com/WZhuo/cc-genai-hook.git "$HOME/Code/cc-genai-hook"
python3 -m venv "$HOME/.local/share/cc-genai-hook/venv"
"$HOME/.local/share/cc-genai-hook/venv/bin/pip" install -U pip setuptools wheel
"$HOME/.local/share/cc-genai-hook/venv/bin/pip" install "$HOME/Code/cc-genai-hook"
CCG="$HOME/.local/share/cc-genai-hook/venv/bin/cc-genai-hook"
"$CCG" --version
```

> 如果用户已经有项目 checkout，也可以把 `pip install` 的路径换成现有 checkout。
> 不要使用 `-e` 指向可能被移动的目录；hook 需要稳定的绝对路径。

### 3b. 执行安装

默认使用 **ordered 模式**：

- Stop 时统一解析 transcript
- 按顺序生成 `chat` / `execute_tool`
- 父 Stop 构建 subagent 子树
- 不需要 sidecar
- 只注册 `Stop` 和 `SessionEnd` hooks

```bash
"$CCG" install \
  --mode command \
  --endpoint "$ENDPOINT" \
  --header "x-singsight-apikey=$API_KEY" \
  --user "$(whoami)"
```

如果 endpoint 已经是完整 `/v1/traces` URL：

```bash
"$CCG" install \
  --mode command \
  --endpoint "$ENDPOINT" \
  --endpoint-kind full \
  --header "x-singsight-apikey=$API_KEY" \
  --user "$(whoami)"
```

### 3c. 可选：realtime 模式

如果用户希望工具 span 在工具结束后立即发送，而不是等 Stop：

```bash
"$CCG" install \
  --mode command \
  --tool-mode realtime \
  --endpoint "$ENDPOINT" \
  --header "x-singsight-apikey=$API_KEY" \
  --user "$(whoami)"
```

realtime 模式会额外注册：

- `PostToolUse`
- `PostToolUseFailure`

注意：

- realtime 模式下子代理不会被展开；subagent 子树只在 ordered 模式构建。
- ordered 是默认，也是推荐模式。

### 3d. 安装器做了什么

- 写入 `~/.claude/genai-hook.json`（权限 `0600`）
- 备份并合并 `~/.claude/settings.json`
- 默认添加：
  - `Stop`
  - `SessionEnd`
- realtime 模式再加：
  - `PostToolUse`
  - `PostToolUseFailure`
- **不会**写入：
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
- `headers` 只显示名字，不显示值
- `hooks` 至少包含 `Stop` 和 `SessionEnd`
- `doctor --send-test` 输出 `test span sent`

如果 `doctor --send-test` 失败：

1. 检查 endpoint 是否能从本机访问。
2. 检查 API key 是否是 **Ingest Key**。
3. 检查 endpoint 是否误带了 `/v1/traces`（base 模式会重复拼接）。
4. 检查是否需要 `--endpoint-kind full`。
5. 检查代理 / 防火墙 / DNS。

可选：直接探测 ingest 路径（空 body 被拒绝是正常的，关注 HTTP 状态码）：

```bash
code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 \
  -X POST "$ENDPOINT/v1/traces" \
  -H "content-type: application/x-protobuf" \
  -H "x-singsight-apikey: $API_KEY" \
  --data-binary "" 2>/dev/null)
echo "HTTP $code"
```

| 状态 | 含义 |
| --- | --- |
| `200` / `202` / `400` / `415` | endpoint 可达，key 被接受（400/415 只是空 body） |
| `401` / `403` | key 被拒绝，创建新的 Ingest Key |
| `404` | 路径或 host 错误，检查 endpoint |
| `000` | 无响应：DNS / 防火墙 / host / port |

---

## Step 5 — 验证 Singsight 数据

让用户新开一个 Claude Code 会话，至少完成一轮对话或工具调用，然后：

1. 打开 Singsight Web UI
2. 进入 **Trace Explorer**
3. 等待约 5 分钟
4. 按以下字段搜索：
   - `gen_ai.conversation.id`：Claude Code session id
   - `enduser.id`：安装时传入的用户
   - `claude_code.genai_hook.step_name`：如 `plan (1 tool) #1`
   - `gen_ai.tool.name` / `gen_ai.tool.call.id`
   - `gen_ai.agent.id`：subagent id

期望结构：

```text
invoke_agent
├── chat
├── execute_tool
└── execute_tool (Agent/Task)
    └── subagent
        ├── chat
        └── execute_tool
```

如果 5 分钟后没有数据：

- 确认用户确实发生了新的模型调用。
- 确认 hook 已安装：`"$CCG" doctor`。
- 查看日志：

```bash
tail -50 "$HOME/.claude/state/genai-hook/hook.err.log"
```

- 用 dry-run 验证本地解析：

```bash
CC_GENAI_HOOK_DRY_RUN=1 claude -p "hello"
cat "$HOME/.claude/state/genai-hook/dry-run.jsonl"
```

dry-run 只写本地文件，不发送到 Singsight。

---

## Step 6 — 与旧的原生 OTel 接入方式的关系

旧版 Singsight Skill 直接设置 Claude Code 原生 OTel：

```text
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_TRACES_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=...
```

本项目 **不需要** 这些变量，使用独立配置文件和 command hooks。

如果用户之前已经按旧方式配置过：

1. 不要自动删除用户的原生 telemetry 设置。
2. 先 **ASK THE USER** 是否要保留。
3. 如果两边同时开启，会出现两套 trace：
   - 原生 `claude_code.*` spans
   - cc-genai-hook 的 `invoke_agent` / `chat` / `execute_tool` spans
4. 建议二选一，避免成本/token 重复统计。

---

## Step 7 — 常见问题

### Q1. `cc-genai-hook: command not found`

CLI 不在 PATH。使用绝对路径，例如：

```bash
"$HOME/.local/share/cc-genai-hook/venv/bin/cc-genai-hook" doctor
```

### Q2. endpoint 404

- Singsight base endpoint 不带 `/v1/traces`。
- 如果用户给的是完整路径，使用 `--endpoint-kind full`。
- 不要用 Web UI 域名猜 collector 域名。

### Q3. 401 / 403

- 使用了 Access Key，而不是 Ingest Key。
- 在 Singsight **Settings → API Keys → Ingest Keys** 重新创建。

### Q4. 没有 subagent 子树

- 确认是 `tool_mode=ordered`（默认）。
- 确认父 transcript 的 Agent/Task tool result 里有 `toolUseResult.agentId`。
- 确认 `subagents/agent-<id>.jsonl` 存在。
- realtime 模式不会展开 subagent。

### Q5. 内容太多 / 包含敏感信息

编辑 `~/.claude/genai-hook.json`：

```json
{
  "content": {
    "mode": "last_turn",
    "include_thinking": false,
    "redact_patterns": ["(?i)bearer\\s+\\S+", "(?i)api[_-]?key\\s*=\\s*\\S+"]
  }
}
```

- `mode=none`：只发结构，不发内容。
- `mode=last_turn`：只发当前 turn。
- 修改后不需要重启 Claude Code，新 hook 进程会读取配置。

### Q6. Python / pip 依赖问题

使用稳定 venv 安装：

```bash
python3 -m venv "$HOME/.local/share/cc-genai-hook/venv"
"$HOME/.local/share/cc-genai-hook/venv/bin/pip" install -U pip setuptools wheel
"$HOME/.local/share/cc-genai-hook/venv/bin/pip" install "$HOME/Code/cc-genai-hook"
```

不要依赖系统 Python 的全局包。

### Q7. `--mode sidecar` 报错

sidecar 尚未实现（计划在 M4）。当前只支持：

```bash
--mode command
```

---

## Step 8 — 升级 / 卸载

升级：

```bash
git -C "$HOME/Code/cc-genai-hook" pull
"$HOME/.local/share/cc-genai-hook/venv/bin/pip" install -U "$HOME/Code/cc-genai-hook"
"$CCG" install --mode command \
  --endpoint "$ENDPOINT" \
  --header "x-singsight-apikey=$API_KEY" \
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

注意：`--purge` 会删除 `~/.claude/genai-hook.json` 和
`~/.claude/state/genai-hook/`，但不会删除 Singsight 里的历史数据。

---

## Step 9 — 汇报变更

完成后向用户汇报：

- endpoint 域名（不要回显 API key）
- 使用的 tool_mode：`ordered` 或 `realtime`
- 写入的文件：
  - `~/.claude/genai-hook.json`
  - `~/.claude/settings.json` hooks
- 安装的 hooks：
  - ordered：`Stop`、`SessionEnd`
  - realtime：再加 `PostToolUse`、`PostToolUseFailure`
- 验证结果：`doctor` / `doctor --send-test`
- 提醒用户在 Singsight Trace Explorer 按 `gen_ai.conversation.id` 或
  `enduser.id` 搜索。

---

## 不要做的事

- 不要把 API key 打印到终端或写进日志。
- 不要使用 gRPC / `4317`；Singsight 只支持 `http/protobuf`。
- 不要为本项目设置 `CLAUDE_CODE_ENABLE_TELEMETRY`、`OTEL_TRACES_EXPORTER` 等原生变量。
- 不要在没有备份的情况下覆盖 `~/.claude/settings.json`。
- 不要删除用户已有的原生 telemetry 配置，除非用户明确同意。
- 不要把 `--mode sidecar` 当成已实现功能。
- 不要在没有 `toolUseResult.agentId` 的情况下强行构造 subagent 子树。
