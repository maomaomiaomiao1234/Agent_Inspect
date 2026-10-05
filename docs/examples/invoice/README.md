# 发票提取任务模板

> 示例资源位于 [examples/invoice](../../../examples/invoice)；下文的 `trace.json`、`profile.json` 等文件名相对于该资源目录或 `init-task` 生成的任务目录。运行这些相对路径命令时，请先进入对应目录。

所有数据都是合成示例。替换 `trace.json` 中的轨迹、最终结果与独立参考数据，再修改 `profile.json` 的预期值和阈值。

普通字段检查使用 `import trace.json --profile profile.json`。复杂业务验收使用 `external.profile.json`：先通过 `evaluator-request` 导出请求，再运行 `python3 evaluator.py request.json results.json`，最后用 `evaluate --profile external.profile.json --results results.json` 导入结果。每个命令使用同一 `--data-dir` 和导入返回的 Run ID。

`evaluator.py` 是可修改的独立程序，不会被导入器自动执行。这里用 Decimal 比较金额；实际任务可以接入数据库查询、人工复核或模型评审。自行运行这些程序前应了解其数据访问与外部调用。结果中的哈希和证据路径必须来自当前请求，不要手工编造。缺少参考数据时返回 `unknown`。

`artifacts.reference_invoice` 应由验收方提供；不要把 agent 自己生成的答案复制成标准答案。
