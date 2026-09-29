# 文档转换评估示例

这些文件来自 `scripts/check_document_conversion.py` 的实际本地验收。PDF 自制，Markdown/JSON 候选为内置模拟；没有运行真实转换 Agent 或 OCR。

| 候选 | 预期结果 | 差异 |
| --- | --- | --- |
| correct | pass | 7 项必需检查全部通过 |
| omitted | fail | 缺少 limitation 正文块 |
| table_error | fail | Tuesday / Sprinkler 从 17 改成 71，定位行3列3（含表头） |
| order_error | fail | method 与 limitation 两段颠倒 |
| missing_reference | inconclusive | 未提供固定参考快照，所有必需检查 unknown |

各场景包括：

- `*.bundle.json`：通用运行、候选输出、源 PDF 的内嵌 base64、参考快照与版本标识。
- `*.request.json`：external evaluator-v1 输入快照。
- `*.results.json`：固定评估器实际输出，绑定 run_id、input_hash、profile_hash。
- `profile.json`：声明的验收规则。

导入 bundle 只保存数据，不执行评估器，也不自动信任保存的结果。重新实际检查：

```sh
uv run agent-review import examples/document_conversion/table_error.bundle.json --data-dir /absolute/review-data
uv run agent-review document-evaluate RUN_ID --data-dir /absolute/review-data
uv run agent-review serve --data-dir /absolute/review-data
```

如需核对跨语言协议：

```sh
uv run agent-review document-evaluator examples/document_conversion/correct.request.json --output /absolute/results.json
uv run agent-review evaluate RUN_ID --profile examples/document_conversion/profile.json --results /absolute/results.json --data-dir /absolute/review-data
```

只接受同一固定 PDF 与标注版本；不是任意 PDF 转换器。OCR 和公式为 unknown 且非必需；若设为必需，整体不得通过。验证详情见 [TASK3_VALIDATION.zh-CN.md](../../TASK3_VALIDATION.zh-CN.md)。
