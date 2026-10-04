# 主动评测中的模型与工具记录

HTTP 任务协议可接入其他 Agent，不限于 smolagents 示例。每类任务仍需独立的 Suite/Profile；目标需要提供适配入口。新增的 `trace` 将实际模型和工具调用接入现有通用轨迹评估，支持行为规则、错误恢复诊断和资源规则。

## 已记录什么

| 材料 | 字段与用途 | 来源 |
| --- | --- | --- |
| 外部任务 | 请求、答案、HTTP 耗时、执行状态 | 评审服务观察 |
| 模型调用 | id、配置的模型名、状态、输入/输出/总 Token、耗时 | 被测服务采集并自报 |
| 工具调用 | id、工具名、参数、结果、状态、读写类型、耗时、关联的模型调用 | 被测服务采集并自报 |
| 任务关联 | job_id、case_id、session_id、turn、run_id | 评审服务生成和绑定 |
| 资源汇总 | 案例的输入/输出/总 Token，模型/工具调用及错误次数，各模型用量 | 从上述材料派生 |

smolagents 示例已对模型后端、三个业务工具和 `final_answer` 加入采集。`output._execution.tools` 保留原来三个业务工具的摘要，协议 `trace.events` 包括 `final_answer`。例如 `Compute 12 + 7` 的 offline 校准会记录两次规划调用、一次 calculator、一次 final_answer；不运行语言模型，Token 和费用保持未知。真实 API 的逐次 Token 来自 SDK 返回值。

工具参数、工具结果、异常类型可以记录；隐藏推理、原始供应商响应和异常正文不导出。已知凭据和常见密钥字段会脱敏。输入/输出 Token 描述资源消耗，不能据此推断模型能力。

## 接入你的 Agent

继续响应 `agent-review/target-v1`，增加可选 `trace`。下面的数字仅展示协议格式：

```json
{
  "protocol": "agent-review/target-v1",
  "output": {"answer": 19},
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

可以参考 `examples/smolagents/telemetry.py` 的 `CallRecorder`。它的工具 Mixin 用于 smolagents；其他框架应包装自己的真实调用入口，输出同一协议。仅提供最终答案的第三方 HTTP API，无法从评审端自动恢复其内部工具和 Token。

约束：每响应最多 200 条调用，响应总大小仍为 256 KiB；id 在当前轮内唯一，parent_id 引用已记录的调用。评审器会验证会话/轮次、Token 类型与上下限、计时值及汇总一致性，不执行记录中的内容。跨轮重复的调用 id 会在通用轨迹中加轮次前缀。

## 汇总和缺失数据

- `usage` 是本轮汇总，`trace.events[].usage` 是逐次模型用量。统计不会把两者相加；完整逐次用量可在省略汇总时推导本轮汇总。两种数据同时完整提供但不一致时，响应会被拒绝。
- 内部记录缺失时不生成虚构模型调用。旧目标仍能提供汇总 Token，但工具/模型次数在主动评测页保持未知。
- partial 轨迹的调用数是可见下界。资源 Profile 无法从不完整轨迹确认满足预算；已观察到工具可确认出现，未观察到工具不能确认缺席。
- 工具与模型耗时之和可能包含并发重叠，不能当作整个任务的墙钟耗时。供应商费用未知时不会使用 Token 价格推算实际账单。
- `complete` 是目标声明，不是独立认证。超时、取消和执行失败保留已有记录，整个案例的覆盖仍为 partial。
- 历史评测不会自动补造调用记录，需要使用新适配器重新运行任务才能采集。

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

根目录 `.env` 不会因为存在而自动加载到所有进程。明确使用 `--env-file .env`，并分别填写评审模型的 `AGENT_REVIEW_LLM_TOKEN` 和被测模型的 `SMOL_MODEL_API_KEY`。它们可使用同一把密钥，但两个变量不会自动互相读取。URL 使用纯文本，不写 Markdown 链接。

```sh
# 终端 A：被测 Agent
uv run --project examples/smolagents --frozen --env-file .env \
  python examples/smolagents/agent_server.py --backend openai --port 9091
# 终端 B：评审服务
uv run --env-file .env agent-review serve --data-dir ./review-data-smolagents \
  --targets examples/smolagents/targets.model.json --port 8765
```
