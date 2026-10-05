# 通用模型用量采集

第二阶段已实现：`agent_trace_review.provider_telemetry` 提供响应规范化、轻量同步/异步包装和按任务启用的 httpx 观察器。Python 自动适配生成的容器已自动接入；手工适配的目标可按下文接入。无需新增模型请求，也不依赖特定框架或安装 OpenAI SDK。

## 接入已有模型客户端

每轮任务创建一个采集器。`client` 使用目标现有的模型客户端；下面以 OpenAI 兼容 SDK 的 Chat Completions 为例，模型和地址来自目标自己的配置：

```python
from agent_trace_review.provider_telemetry import CONTEXT_HEADERS, ModelRecorder

def run_task(task, client, api_url, model, incoming_headers):
    recorder = ModelRecorder(
        session_id=task["session_id"], turn=task["turn"],
        api_url=api_url, model=model,
        context={key: incoming_headers[header]
                 for key, header in CONTEXT_HEADERS.items()
                 if header in incoming_headers},
    )
    try:
        with recorder.collect():
            result = client.chat.completions.create(
                model=model, messages=task["messages"],
            )
        return {"protocol": "agent-review/target-v1", "usage_mode": "model",
                "output": result.choices[0].message.content,
                "trace": recorder.trace()}
    except Exception:
        return {"protocol": "agent-review/target-v1", "usage_mode": "model",
                "execution_status": "error", "error": "agent_execution_failed",
                "output": None, "trace": recorder.trace()}
```

`collect()` 只观察该上下文内、配置地址下的 `POST /chat/completions` 和 `POST /responses`。其他端点、健康检查，以及上下文外的评审模型调用不计入。SDK 的每次可见 HTTP 重试是独立调用，不对调用 ID 或供应商响应 ID 相同的不同请求自行合并。

上例只演示模型接入，现有 Agent 应将 `collect()` 包在其真实运行入口外。此模块不自动包装业务工具。默认 `trace()` 的覆盖为 `partial`；只有完整记录该轮全部模型、工具和子 Agent 调用时，才使用 `trace(coverage="complete")`。完整模型 Token 不证明工具轨迹完整。

## 流式响应与原生包装

httpx 观察器支持同步和异步 JSON/SSE，沿实际消费的响应读取字段，不预先缓存整个流。流式客户端应正常消费并关闭流。例如：

```python
with recorder.collect():
    with client.chat.completions.create(
        model=model, messages=messages, stream=True,
        stream_options={"include_usage": True},
    ) as stream:
        for chunk in stream:
            handle_chunk(chunk)
```

Responses 使用响应完成事件中的 `response.usage`。Chat Completions/DeepSeek 的用量快照覆盖该次请求的旧快照，不将每个 chunk 当成新调用，也不累加重复快照。未收到完整结束标记、流被提前关闭、取消或发生读取错误时，保留已观察用量并标为下界。没有 usage 的调用保留未知；显式返回 0 才记录 0。

其他原生模型入口可用：

```python
result = recorder.model("native-model", native_generate, prompt)
result = await recorder.async_model("native-model", native_async_generate, prompt)
```

接受响应的 `usage`、`usage_metadata` 或 `token_usage` 属性，也支持返回同步/异步 SDK chunk 迭代器。流包装支持 `with` / `async with`，应显式关闭。httpx 观察与显式包装同时启用时，优先保留每次实际 HTTP 请求，嵌套包装不额外计数。未经过 httpx 的函数内部重试不能自动拆分；应将包装放在每次实际请求处。

仅需要转换已有响应时，可调用 `normalize_usage(response)`，返回 `{"tokens": ...}` 或 `None`。这是字段映射，不是 Token 估算器。

## 字段及关联

| 标准字段 | 供应商字段 | 含义 |
| --- | --- | --- |
| `tokens.input` | `prompt_tokens` / `input_tokens` | 输入 Token |
| `tokens.output` | `completion_tokens` / `output_tokens` | 输出 Token |
| `tokens.total` | `total_tokens`；缺失时由完整 input + output 派生 | 总 Token |
| `tokens.reasoning` | completion/output details 的 `reasoning_tokens` | 输出的推理细分 |
| `tokens.cache_read` | prompt/input details 的 `cached_tokens` / DeepSeek `prompt_cache_hit_tokens` | 输入的缓存命中细分 |
| `tokens.cache_write` | prompt/input details 的 `cache_write_tokens` | 输入的缓存写入细分 |
| `tokens.cache_miss` | DeepSeek `prompt_cache_miss_tokens` | 输入的缓存未命中细分 |

缓存和推理细分不会再次加到 total。没有供应商字段时不推断缓存未命中或费用。新增的缓存摘要字段为 `cache_read_tokens/cache_write_tokens/cache_miss_tokens`；旧五字段 `usage_summary` 继续接受，历史记录不补写。

每次调用的 `context` 保留 `assessment_id/case_id/budget_id/attempt/session_id/turn/call_id`，以及存在时的 `requested_model`、`response_id` 和 `provider_request_id`（HTTP `x-request-id`）。`model` 保存响应中实际返回的模型标识；未返回时保留请求模型。`usage_source=provider_reported` 表示字段来自供应商响应，整个目标轨迹的来源仍是 `target_reported`，不构成账单认证。

评测器通过 `X-Agent-Review-Assessment-Id/Case-Id/Budget-Id/Attempt` 请求头传递关联信息；`session_id/turn` 仍在原协议 JSON 中。旧目标可以忽略这些头。自动适配器读取并传播它们，评测器写入通用轨迹时再绑定自己的任务标识。它们不会自动发送给模型供应商。

上下文使用 `contextvars`，可随 asyncio 子任务和 `asyncio.to_thread` 传播。原生线程池需为每个提交调用 `contextvars.copy_context().run`；跨进程、独立服务和后台任务需显式传递关联信息。不能按时间重叠猜测归属。每个任务轮次单独创建 recorder，子 Agent 必须在该上下文内调用。

网页的「模型与工具调用 → Token 采集状态与原因」展示缓存/推理细分；运行详情的模型事件展示已知用量、缺失原因和请求/响应 ID。Markdown、JSON 和证据包保留这些数据。`usage_complete=false` 的单次消耗不会被完整 trace 自动升级成完整总额。

## 覆盖边界和验证

固定运行模板继续应用已有输出预算和期限；通用 `ModelRecorder` 本身只采集，不更改请求参数或重试策略。自动适配器的采集模块会复制到构建目录、计算哈希并包含在适配文件 ZIP 中。

HTTP 观察器覆盖配置端点的 httpx 请求和上述响应协议。其他网络库、未传播上下文的线程/进程、绕过配置端点的调用仍可能缺失。流解析保留有界缓冲，过大/损坏事件标记采集不完整；后续可解析的 usage 仍保留。目标进程崩溃或评测 HTTP 请求超时且没有返回响应时，进程内记录不能由评测器恢复；可启用后续实现的 [模型网关试点](MODEL_GATEWAY.zh-CN.md) 保存评审端观测；该采集模块本身仍为进程内方案。

控制数据验证覆盖两次 10/5/15 调用得到 20/10/30，两轮得到 40/20/60，以及流式末尾、重试、取消、并发、嵌套包装、重复导入、缓存/推理细分和评审用量分离。实际 SDK 使用安装的 OpenAI 3.24.0 与 MockTransport，实际 Docker 使用本地合成供应商。详见 [第二阶段验收](../PROVIDER_TELEMETRY_VALIDATION.zh-CN.md)。未新增付费模型调用。

字段依据（2026-10-05 核对）：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI Responses 流式事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)。
