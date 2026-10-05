# 主动评测中的模型与工具记录

HTTP 任务协议可接入其他 Agent，不限于 smolagents 示例。每类任务仍需独立的 Suite/Profile；目标需要提供适配入口。新增的 `trace` 将实际模型和工具调用接入现有通用轨迹评估，支持行为规则、错误恢复诊断和资源规则。

## 已记录什么

| 材料 | 字段与用途 | 来源 |
| --- | --- | --- |
| 外部任务 | 请求、答案、HTTP 耗时、执行状态 | 评审服务观察 |
| 模型调用 | id、请求/返回模型、供应商请求 ID、状态、输入/输出/总 Token、缓存/推理细分、耗时 | 被测服务采集并自报 |
| 工具调用 | id、工具名、参数、结果、状态、读写类型、耗时、关联的模型调用 | 被测服务采集并自报 |
| 任务关联 | assessment_id/job_id、case_id、budget_id、attempt、session_id、turn、call_id、run_id | 评审服务生成和绑定 |
| 资源汇总 | 案例的输入/输出/总 Token，模型/工具调用及错误次数，各模型用量 | 从上述材料派生 |

smolagents 示例已对模型后端、三个业务工具和 `final_answer` 加入采集。`output._execution.tools` 保留原来三个业务工具的摘要，协议 `trace.events` 包括 `final_answer`。例如 `Compute 12 + 7` 的 offline 校准会记录两次脚本规划调用、一次 calculator、一次 final_answer；不调用模型服务，Token 和费用显示“不适用（离线校准）”，不会伪造零用量。真实 API 的逐次 Token 来自 SDK 返回值。

工具参数、工具结果、异常类型可以记录；隐藏推理、原始供应商响应和异常正文不导出。已知凭据和常见密钥字段会脱敏。输入/输出 Token 描述资源消耗，不能据此推断模型能力。

## 接入你的 Agent

继续响应 `agent-review/target-v1`，增加可选 `trace`。下面的数字仅展示协议格式：

```json
{
  "protocol": "agent-review/target-v1",
  "output": {"answer": 19},
  "usage_mode": "model",
  "trace": {
    "trace_version": "agent-review/target-trace-v1",
    "session_id": "请求中的session_id",
    "turn": 0,
    "coverage": "complete",
    "events": [
      {
        "id": "model-1", "kind": "llm", "model": "你的模型",
        "status": "completed", "duration_ms": 200,
        "usage": {"tokens": {"input": 20, "output": 8, "total": 28}}
      },
      {
        "id": "tool-1", "kind": "tool", "tool": "calculator",
        "parent_id": "model-1", "status": "completed", "effect": "read",
        "input": {"a": 12, "b": 7, "operation": "add"},
        "output": {"answer": 19}, "duration_ms": 1.2
      }
    ]
  }
}
```

接入步骤：

1. 在每轮任务开始时重置该轮采集器，保留 Agent 的正常会话记忆。
2. 包装实际模型调用，记录 SDK 返回的用量；供应商没返回的字段省略。
3. 包装实际工具入口，包含参数校验失败和执行异常。用单调时钟计时；不使用 HTTP 总耗时代替每次模型或工具耗时。
4. 将采集结果绑定请求中的 `session_id` 和 `turn`，随答案返回。完整覆盖当前轮所有调用时才声明 `complete`；缺少子 Agent、后台工具或模型请求时使用 `partial`。
5. 失败任务可返回 `execution_status: "error"`、`error: "agent_execution_failed"`、`output: null` 和已采集的 trace，评审器会保留证据并停止该案例后续轮次。HTTP 错误仍按原有失败路径处理。

通用模型入口可使用 `provider_telemetry.ModelRecorder`，支持 httpx JSON/SSE 和原生同步/异步模型包装，见 [通用采集指南](PROVIDER_TELEMETRY.zh-CN.md)。工具包装可以参考 `examples/smolagents/telemetry.py` 的 `CallRecorder`。它的工具 Mixin 用于 smolagents；其他框架应包装自己的真实调用入口，输出同一协议。仅提供最终答案的第三方 HTTP API，无法从评审端自动恢复其内部工具和 Token。

约束：每响应最多 200 条调用，响应总大小仍为 256 KiB；id 在当前轮内唯一，parent_id 引用已记录的调用。评审器会验证会话/轮次、Token 类型与上下限、计时值及汇总一致性，不执行记录中的内容。跨轮重复的调用 id 会在通用轨迹中加轮次前缀。

## 汇总和缺失数据

- `usage` 是本轮汇总，`trace.events[].usage` 是逐次模型用量。每个字段优先使用汇总，统计不会把两者相加；完整逐次用量可补齐遗漏的汇总字段。已提供字段与完整调用冲突，或汇总小于可见调用下界时，响应会被拒绝。total 缺失而 input/output 齐全时派生 total。
- 内部记录缺失时不生成虚构模型调用。旧目标仍能提供汇总 Token，但工具/模型次数在主动评测页保持未知。
- partial 轨迹的调用数是可见下界。Token 完整性与工具轨迹覆盖分别判断：即使工具轨迹缺失，完整的本轮汇总也能用于资源规则；只有部分用量时，已超过预算可判 fail，未超过仍为 unknown。已观察到工具可确认出现，未观察到工具不能确认缺席。
- 工具与模型耗时之和可能包含并发重叠，不能当作整个任务的墙钟耗时。供应商费用未知时不会使用 Token 价格推算实际账单。
- `complete` 是目标声明，不是独立认证。超时、取消和执行失败保留已有记录，整个案例的覆盖仍为 partial。
- 历史评测不会自动补造调用记录，需要使用新适配器重新运行任务才能采集。

### 逐字段完整性与诊断

`TargetResponse.usage_mode` 可选值为 `model`、`offline`、`unknown`（默认）。只有目标明确声明 `offline` 才显示不适用；`demo: true` 不足以判断是否调用模型。离线响应不能同时提供模型用量。

评测 JSON 的 `results[].usage.fields`、预算的 `curves[].usage.fields` 和通用轨迹的 `usage_summary.fields` 使用同一格式，包含 `input_tokens`、`output_tokens`、`total_tokens`、`reasoning_tokens`、`cost_usd`，新记录还包含 `cache_read_tokens`、`cache_write_tokens`、`cache_miss_tokens`。旧五字段摘要继续兼容。例如已采集一次 10/5/15 的调用，随后另一次调用失败且未返回用量：

```json
{"total_tokens": {"value": 15, "status": "partial", "source": "calls", "reason": "执行未完成或仍有未完成案例，仅表示已观测下界。"}}
```

|status|显示|含义|
|---|---|---|
|complete|15|此字段完整；可以判定预算是否满足|
|partial|≥15|已观测下界；失败、取消、超时和未完成案例仍保留消耗|
|unknown|未知|未上报字段，不补零、不从最终答案长度估算|
|not_applicable|不适用（离线校准）|目标明确声明离线模式，没有模型服务调用|

`source` 为 `aggregate`（轮次汇总）、`calls`（逐次调用）、`mixed`（不同轮次选取不同来源）、`none` 或 `offline`。原有 `usage.input_tokens/output_tokens/total_tokens/cost_usd` 字段继续仅保存完整总量，部分值在 `fields.*.value`；旧消费者不会误把下界当作完整用量。

通用轨迹可选 `usage_summary` 提供独立资源证据，格式为 `{"provenance": "target_reported", "fields": 上述完整五字段字典}`。运行详情和 Profile 优先使用此摘要，保留实际 llm 事件但不重复累加，也不生成虚构模型事件。摘要须与可见调用一致，不能小于调用下界。

目标 `/health` 可声明 `usage_mode` 和 `usage_collection`（`call_usage` / `aggregate_usage` / `not_applicable`），网页「检查 Token 采集」调用 `GET /api/targets/{id}/telemetry-readiness`；只检查已登记服务的健康接口，不执行任务或启动容器。采集声明不证明供应商实际返回了所有字段，每次响应仍单独判断完整性。未配置健康接口、未启动 Docker 目标或未声明采集时保留未知。

当前包含通用 SDK/HTTP 包装，仍依赖目标采集和自报；尚未包含受控模型网关。评审模型 Token 与被测 Agent 用量分别保存；费用未知时不会按模型价格伪造账单。

后续新增的 [Python 自动适配](AUTO_ADAPTATION.zh-CN.md) 提供固定容器运行模板，可观察已配置模型端点的 httpx JSON/SSE 请求，以及显式包装的原生模型/工具调用；这不等同于全框架遥测或受控模型网关。其可选 `adapter_evidence` 保存源文件路径、符号、哈希、入口是否被观察及有界异常类型。自动适配任务逐轮校验这些自报证据，失败时保留已有用量并停止确认能力通过。

## 查看和导出

主动评测详情页的「模型与工具调用」显示各案例的 Token、调用/错误次数、耗时和覆盖范围；可展开「按模型查看用量」。点击「查看对话和验收」，在「执行轨迹」选择模型/工具，查看参数、输出、状态、用量、耗时及来源关联。

Markdown 包含汇总；评测 JSON 包含结构化汇总；证据包包含逐次事件和原始脱敏记录。也可使用 CLI：

```sh
uv run agent-review assessment-report assessment_ID --data-dir ./review-data \
  --format bundle --output ./reports/assessment.bundle.json
uv run agent-review schema --output ./schema.json
```

Profile 中可以加入 `tool_required` / `tool_forbidden`，以及 `/metrics/tokens_total`、`/metrics/llm_calls`、`/metrics/tool_calls`、`/metrics/llm_duration_ms`、`/metrics/tool_duration_ms` 等规则。保留任务本身的结果验收规则；过程和资源检查通过不能单独证明答案正确。

## 根目录 .env 的加载

`agent-review serve` 自动读取当前目录 `.env`。网页仓库评测自动选择专用 `AGENT_REVIEW_TARGET_*`、有密钥的 `SMOL_MODEL_*`，或复用 `AGENT_REVIEW_LLM_*` 的完整配置；缺少配置会提示，不再要求手动模型变量映射。网页直接启动见 [仓库评测指南](REPOSITORY_ASSESSMENT.zh-CN.md#网页直接运行真实模型评测)。

独立启动 smolagents 示例进程时，仍需明确使用 `--env-file .env` 并填写 `SMOL_MODEL_API_KEY`；独立示例不会读取评审模型变量。URL 使用纯文本，不写 Markdown 链接。

```sh
# 终端 A：被测 Agent
uv run --project examples/smolagents --frozen --env-file .env \
  python examples/smolagents/agent_server.py --backend openai --port 9091
# 终端 B：评审服务
uv run --env-file .env agent-review serve --data-dir ./review-data-smolagents \
  --targets examples/smolagents/targets.model.json --port 8765
```
