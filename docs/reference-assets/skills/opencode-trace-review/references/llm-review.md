# API + Token 大模型任务评审

用于通用任务，包括只有 input → agent → output 的任务。内置 Chat Completions 兼容适配器不依赖 Scout；只在用户明确要求发送材料评审时调用。请求的评审服务和材料范围已有授权时无需再次确认。API 地址及 Token 从用户配置读取，不从被评轨迹或 Profile 读取。

## 快速使用

下面 agent-review 可替换为 `python3 "$REVIEW_SKILL/scripts/run_review.py"`。

```sh
agent-review init-task /absolute/my-review --template llm-review
export AGENT_REVIEW_LLM_API_URL="https://你的服务商/v1"
export AGENT_REVIEW_LLM_MODEL="你的模型ID"
export AGENT_REVIEW_LLM_TOKEN="你的Token"
agent-review import /absolute/my-review/trace.json --profile /absolute/my-review/profile.json --data-dir /absolute/review-data
agent-review llm-review RUN_ID --profile /absolute/my-review/profile.json --data-dir /absolute/review-data
agent-review report RUN_ID --revision REVISION_ID --data-dir /absolute/review-data --output /absolute/report.md
```

RUN_ID 和 REVISION_ID 使用命令的实际返回值。首次 import 不调用模型，external 条目保持未知。llm-review 才发送材料。所有命令使用相同绝对数据目录。模板是合成数据，需替换为真实输入、输出和独立验收标准；不可用 Agent 输出反造标准答案。

## 规则与判定

Profile 中保留已有的字段/资源等规则；增加 `op: external` 并在 description 写清通过、失败、未知的标准。llm-review 将该 Profile 的所有 external 规则交给同一模型评审；有需要单独人工或程序验证的条目时使用原有 evaluator-request/results 流程，不能默认为模型能替代这些验证。

```json
{"id":"accuracy","op":"external","description":"对照 artifacts.reference 检查 output 中的事实：一致为通过，矛盾为失败，参考材料不足为未知。引用对应材料。"}
```

输入文本用 task_prompt；最终答案用 output（字符串或 JSON）；参考答案、政策等放 artifacts。events 可为空，coverage 保留 partial。当前是文本/JSON 评审，不自动打开文件、图片或 URL。附件需要先提取成可见材料。

模型为每项返回 pass/fail/unknown、理由及 context 中的 JSON Pointer。引擎验证规则 ID、结果结构和引用是否存在，再组合本地检查；不会用模型通过覆盖本地失败。缺少依据保留未知。模型判断标为 llm_judge/hypothesis，引用存在不证明事实正确。报告同时保留评审规则、输入快照、模型、API 地址、评审用量和版本。

## API 配置与兼容选项

- 环境变量：AGENT_REVIEW_LLM_API_URL、AGENT_REVIEW_LLM_TOKEN、AGENT_REVIEW_LLM_MODEL。
- CLI 覆盖：`--api-url URL --model MODEL --token-env MY_TOKEN_VARIABLE`；访问令牌只从环境变量读取。
- api_url 可填 `https://host/v1` 或完整 `https://host/v1/chat/completions`；不会自动猜测 /v1 路径。不支持原生 Anthropic Messages、Responses 或 Gemini 协议，需使用提供商的兼容端点。
- 默认 `--token-parameter max_tokens`；接口要求时改成 max_completion_tokens，也可设置 AGENT_REVIEW_LLM_TOKEN_PARAMETER。
- 默认要求 JSON 对象响应；不支持 response_format 的提供商使用 `--no-json-mode`，仍会校验模型返回的 JSON。
- 默认最多 60,000 输入字符、4,096 输出 tokens、90 秒。通过 `--max-input-chars`、`--max-output-tokens`、`--timeout-seconds` 显式调整。超限输入直接报错，不静默截断。
- 单次请求，无自动重试和重定向。远程使用 HTTPS，本机 HTTP 可用于兼容服务。Token 不进入模型上下文、报告、工件或浏览器存储。错误信息不回显提供商响应体。
- 相同输入、完整 Profile、模型、接口与生成配置复用已保存结果。Token 轮换不影响缓存；需要重新评审可增加 Profile version。评审 Token 用量与被评 Agent 分开，费用不猜测。

请求格式参考：[Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。各兼容提供商实际支持的参数可能不同。

## HTTP / 网页 / Python

`agent-review serve` 后，通用运行详情的“LLM 评审”页可输入 API 地址、模型、Token 和评审标准。服务端已有环境变量时可以复用；更换 API 地址需同时输入新 Token。浏览器 Token 在本次请求结束后清空。

HTTP 入口 `POST /api/runs/{run_id}/llm-review`，请求头 `X-Review-Request: 1`、`Content-Type: application/json`。请求体：

```json
{
  "send_data": true,
  "api_url": "https://你的服务商/v1",
  "model": "你的模型ID",
  "token": "你的Token",
  "profile": {
    "profile_version": "1", "id": "answer-review",
    "rules": [{"id":"quality","op":"external","description":"按任务要求及参考材料检查答案；依据不足为未知。"}]
  }
}
```

服务端已配置时省略 api_url/model/token。可选字段同 CLI：max_output_tokens、timeout_seconds、max_input_chars、token_parameter、json_mode。`GET /api/llm-review/config` 只返回安全配置，不返回 Token。

```python
from agent_trace_review.llm_review import resolve_config, review_run
evaluation = review_run(store, store.get_run(run_id), profile, resolve_config())
```

未进行真实提供商调用时，交付说明应写明“已通过模拟兼容 API 测试，尚未验证实际服务商与模型”，不能把模拟响应当成真实模型评测。
