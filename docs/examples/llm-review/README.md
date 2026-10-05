# 输入输出的大模型评审示例

> 示例资源位于 [examples/llm-review](../../../examples/llm-review)；下文的 `trace.json`、`profile.json` 等文件名相对于该资源目录或 `init-task` 生成的任务目录。运行这些相对路径命令时，请先进入对应目录。

这是合成示例。将 trace.json 中的 task_prompt、output 和参考材料，以及 profile.json 中的标准替换为自己的任务。没有过程记录时保留 coverage: partial 和 events: []。

配置 API 地址、模型和 Token 后，先导入再显式评审：

```sh
export AGENT_REVIEW_LLM_API_URL="https://你的服务商/v1"
export AGENT_REVIEW_LLM_MODEL="你的模型ID"
export AGENT_REVIEW_LLM_TOKEN="你的Token"
agent-review import trace.json --profile profile.json --data-dir ./review-data
agent-review llm-review RUN_ID --profile profile.json --data-dir ./review-data
agent-review report RUN_ID --data-dir ./review-data --output report.md
```

RUN_ID 使用 import 返回值。导入只检查本地规则，external 项在模型评审前保持未知。以上命令均在同一目录运行。

接口需兼容 Chat Completions；API 地址可填基础地址（末尾应包含服务商要求的 /v1 等路径）或完整 /chat/completions 地址。默认发送 max_tokens；要求 max_completion_tokens 的模型添加 `--token-parameter max_completion_tokens`。不支持 JSON 模式的接口添加 `--no-json-mode`。

评审会发送任务输入、输出、内嵌材料、可见事件和规则，可能产生 API 费用。Token 仅用于 Authorization header，不放入提示词、报告或存储。不要把真实 Token 写进示例文件或提交 Git。

模型结论参与 Profile 验收，并标明模型来源；不覆盖本地规则失败。未提供可靠参考材料时，事实核验可能为 unknown。此示例不会自动运行被测 Agent。
