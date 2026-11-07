# cc-genai-hook

通用的 Claude Code GenAI hook。它通过 Claude Code hooks 读取会话 transcript，生成符合
OpenTelemetry GenAI semantic conventions 的 span，并导出到**任意 OTLP HTTP/protobuf 后端**。

- 不绑定具体后端：只配置 OTLP endpoint 和任意 header。
- 不依赖 Claude Code 原生 trace：使用独立配置文件，不需要开启原生 telemetry。
- 确定性 trace/span ID：重复 hook 重放得到相同的 ID。
- 内容默认脱敏、可截断、可关闭。
- 支持 command 基线模式和 sidecar 实时模式（sidecar 在 M2 实现）。

## 当前状态

**M1（已完成）**

- `command` 模式：`Stop`、`SessionEnd` command hooks
- transcript 解析：`promptId` 分组、assistant 多行合并、tool_result 索引
- `invoke_agent` segment root + `chat` spans
- 确定性 trace_id / span_id
- SQLite 幂等状态，重复 Stop/SessionEnd 不重复发送
- 通用 OTLP endpoint / headers 配置
- `install`、`uninstall`、`doctor`、`config`、`seed`、`hook` CLI
- dry-run JSONL、本地 OTLP receiver 示例

**M2 / M3 / M4（计划中）**

- M2：sidecar/HTTP hooks、实时 `PostToolUse`、`execute_tool` spans
- M3：子代理 `agent_transcript_path`、Agent tool 映射、同一 trace
- M4：durable outbox、采样、完整脱敏、TTL 清理

## 工作原理

```text
Claude Code
  │
  ├─ Stop / SessionEnd command hook
  │      │
  │      ▼
  │  cc-genai-hook hook stop
  │      │ 读 transcript JSONL
  │      │ 按 promptId 分组 assistant 消息
  │      │ 生成 invoke_agent / chat spans
  │      ▼
  │  OTLP HTTP/protobuf
  │      │
  │      ▼
  └─ 任意 OTLP 后端（SignSight / SigNoz / Jaeger / Tempo / Collector ...）
```

trace 拓扑：

```text
invoke_agent（segment root）
└── chat（每条 assistant 消息）
```

M2 会增加：

```text
invoke_agent
├── chat
└── execute_tool
```

## 安装

要求 Python 3.9+（已在 3.9.6 验证）。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip setuptools wheel
pip install -e .
```

### 通用 OTLP 后端

```bash
cc-genai-hook install \
  --mode command \
  --endpoint https://otel.example.com \
  --header x-api-key="$OTEL_API_KEY" \
  --header "Authorization=Bearer $OTEL_TOKEN" \
  --user "$(whoami)"
```

### SignSight 示例

代码不依赖 SignSight；下面只是配置示例：

```bash
cc-genai-hook install \
  --mode command \
  --endpoint https://dev-otel.clickzetta.com \
  --header x-singsight-apikey="$SINGSIGHT_API_KEY" \
  --user "$(whoami)"
```

### 完整 `/v1/traces` 路径

```bash
cc-genai-hook install \
  --mode command \
  --endpoint https://backend.example.com/v1/traces \
  --endpoint-kind full \
  --header "Authorization=Bearer $OTEL_TOKEN"
```

安装器会写入：

```text
~/.claude/genai-hook.json         # 0600
~/.claude/settings.json           # 合并 Stop / SessionEnd hooks，并自动备份
```

安装器**不会**写入 `CLAUDE_CODE_ENABLE_TELEMETRY`、`OTEL_TRACES_EXPORTER`、
`OTEL_LOG_*` 等 Claude Code 原生 telemetry 变量。

## 配置

默认路径：`~/.claude/genai-hook.json`。完整示例见
[`examples/genai-hook.example.json`](examples/genai-hook.example.json)。

```json
{
  "version": 1,
  "enabled": true,
  "exporter": {
    "protocol": "http/protobuf",
    "endpoint": "https://otel.example.com",
    "endpoint_kind": "base",
    "headers": {
      "x-api-key": "${OTEL_API_KEY}",
      "Authorization": "Bearer ${OTEL_TOKEN}"
    },
    "compression": "gzip",
    "timeout_ms": 30000,
    "batch": {
      "max_size": 512,
      "schedule_delay_ms": 500
    }
  },
  "resource": {
    "service.name": "claude-code",
    "attributes": {
      "enduser.id": "${USER}"
    }
  },
  "content": {
    "mode": "last_turn",
    "include_thinking": false,
    "max_input_chars": 200000,
    "max_message_chars": 100000,
    "max_tool_chars": 100000,
    "redact_patterns": []
  },
  "compat": {
    "messages_format": "json",
    "cache_write_attribute": "cache_creation",
    "emit_provider_name": true,
    "emit_system_legacy": true
  },
  "hook": {
    "sample_rate": 1.0,
    "dry_run": false,
    "debug": false,
    "agent_name": "claude-code"
  },
  "state": {
    "dir": "~/.claude/state/genai-hook",
    "ttl_days": 14
  }
}
```

### Endpoint 规则

| 配置 | 行为 |
| --- | --- |
| `exporter.endpoint` + `endpoint_kind=base` | 自动追加 `/v1/traces` |
| `exporter.endpoint` + `endpoint_kind=full` | 按原样使用 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 作为 base，自动追加 `/v1/traces` |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | 按原样使用，优先级最高 |

### Header 规则

优先级：

```text
OTEL_EXPORTER_OTLP_TRACES_HEADERS
  > OTEL_EXPORTER_OTLP_HEADERS
  > config.exporter.headers
```

支持 `${ENV_VAR}` 展开。复杂 header 建议直接写 JSON 配置，不要塞进 `OTEL_EXPORTER_OTLP_HEADERS`。

### 环境变量

| 变量 | 作用 |
| --- | --- |
| `CC_GENAI_HOOK_CONFIG` | 配置文件路径 |
| `CC_GENAI_HOOK_ENABLED` | 总开关 |
| `CC_GENAI_HOOK_DRY_RUN` | 只写 `dry-run.jsonl`，不发送 |
| `CC_GENAI_HOOK_DEBUG` | debug 日志 |
| `CC_GENAI_HOOK_CONTENT_MODE` | `last_turn` / `full` / `none` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 通用 OTLP base endpoint |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | traces 专用 endpoint |
| `OTEL_EXPORTER_OTLP_HEADERS` | 通用 OTLP header |
| `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | traces 专用 header |
| `OTEL_RESOURCE_ATTRIBUTES` | 合并进 resource |

## 验证

```bash
cc-genai-hook doctor
cc-genai-hook doctor --send-test
cc-genai-hook config show
cc-genai-hook config validate
```

dry-run：

```bash
CC_GENAI_HOOK_DRY_RUN=1 claude -p "hello"
cat ~/.claude/state/genai-hook/dry-run.jsonl
```

本地 OTLP receiver 端到端验证：

```bash
# 终端 1
python3 examples/local_otlp_receiver.py --port 4318

# 终端 2
cc-genai-hook install \
  --endpoint http://127.0.0.1:4318 \
  --header x-api-key=test \
  --user "$(whoami)"
cc-genai-hook doctor --send-test
```

receiver 会打印每个 span 的 `trace_id`、`span_id`、`parent_span_id`、属性和事件。

## CLI

```text
cc-genai-hook install [--mode command|sidecar|auto]
                      [--endpoint URL] [--endpoint-kind base|full]
                      [--header KEY=VALUE]... [--user NAME]
                      [--content-mode last_turn|full|none]
                      [--dry-run]
cc-genai-hook uninstall [--purge]
cc-genai-hook doctor [--send-test]
cc-genai-hook config show [--redact]
cc-genai-hook config validate
cc-genai-hook seed --session-id ID --transcript PATH
cc-genai-hook hook stop|session_end|subagent_stop|seed
```

M1 只实现了 `--mode command`。`--mode sidecar` 会在 M2 提供。

### seed

安装后第一次 `Stop` 会把现有 transcript 的历史 assistant 消息全部补发。如果不想回灌历史：

```bash
cc-genai-hook seed --session-id <session-id> --transcript <transcript-path>
```

## Span 与 gen_ai 字段

### `invoke_agent`

- `gen_ai.operation.name=invoke_agent`
- `gen_ai.provider.name=anthropic`
- `gen_ai.agent.name=claude-code`
- `gen_ai.conversation.id`
- `gen_ai.input.messages` / `gen_ai.output.messages`
- `gen_ai.usage.input_tokens` / `output_tokens` / `cache_read` / `cache_creation`
- `gen_ai.response.finish_reasons`
- `claude_code.genai_hook.synthetic=true`
- `claude_code.genai_hook.turn.prompt_id`
- `claude_code.genai_hook.turn.segment`

### `chat`

- `gen_ai.operation.name=chat`
- `gen_ai.provider.name=anthropic`
- `gen_ai.request.model` / `gen_ai.response.model`
- `gen_ai.response.id`
- `gen_ai.response.finish_reasons`
- `gen_ai.conversation.id`
- `gen_ai.usage.*`
- `gen_ai.input.messages` / `gen_ai.output.messages`
- `claude_code.genai_hook.message.uuid`
- `claude_code.genai_hook.request_ref`
- `claude_code.genai_hook.timing.source=transcript`

### 内容处理

- `content.mode=last_turn`：只发当前 turn。
- `content.mode=full`：发完整会话历史。
- `content.mode=none`：只发结构，不发内容。
- `include_thinking=false`：默认不发 thinking。
- `redact_patterns`：正则替换敏感内容。
- 截断后写 `claude_code.genai_hook.*.truncated=true`。

## 状态与幂等

- 状态目录：`~/.claude/state/genai-hook/`
- SQLite 表：`sessions`、`segments`、`messages`
- 以 `(session_id, message_id)` 为幂等键，重复 Stop/SessionEnd 不会重复发 chat。
- 确定性 span_id 作为第二层保护：即使状态丢失，重放也会得到相同 ID。
- `segments` 在导出成功后才标记完成；flush 失败不会把消息标记为已处理。

## 安全与隐私

- Command hooks 以当前用户权限运行。
- `~/.claude/genai-hook.json` 包含凭证，权限 `0600`。
- Hook 失败不会阻塞 Claude Code：入口捕获异常并 `exit 0`。
- 失败日志：`~/.claude/state/genai-hook/hook.err.log`。
- 内容可能包含 prompt、文件内容和工具输出；请用 `content.mode` 和 `redact_patterns` 控制。
- 不开启 Claude Code 原生 trace，避免与合成 trace 重复计数。

## 目录结构

```text
cc-genai-hook/
├── pyproject.toml
├── README.md                 # 中文
├── README.en.md              # English
├── docs/
│   └── design.md             # 设计文档
├── examples/
│   ├── genai-hook.example.json
│   ├── settings.command.json
│   ├── backend-singsight.json
│   └── local_otlp_receiver.py
├── src/cc_genai_hook/
└── tests/
```

## 开发

```bash
PYTHONPATH=src python3 -m pytest -q
```

当前测试：25 passed。

## Roadmap

| 阶段 | 内容 |
| --- | --- |
| M1 | command 模式、Stop/SessionEnd、chat/root spans、SQLite 幂等 |
| M2 | sidecar、HTTP hooks、PostToolUse/Failure、execute_tool |
| M3 | 子代理 agent_transcript_path、Agent tool 映射、同一 trace |
| M4 | durable outbox、采样、完整脱敏、TTL 清理 |

详细设计见 [`docs/design.md`](docs/design.md)。

## English

[English README](README.en.md)

## License

未指定。使用前请与仓库所有者确认。
