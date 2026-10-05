# 代码修复验证材料

> 示例资源位于 [examples/code_repair](../../../examples/code_repair)；下文命令在项目根目录执行，涉及 `trace.json` 等相对文件名时使用该资源目录中的文件。

这四个 generic/2 包保存于 2026-09-29 的 Task 2 验收。候选由内置模拟器生成；baseline/final 的测试日志与 JUnit 来自本机实际 Docker 执行。没有运行真实 Agent 或模型。

| 文件 | 结果 | 说明 |
| --- | --- | --- |
| correct.bundle.json | pass | 三个固定测试全通过 |
| incorrect.bundle.json | fail | 到期边界仍失败 |
| regression.bundle.json | fail | 原本通过的过期前有效测试变成失败 |
| timeout.bundle.json | inconclusive | 最终阶段超过 10 秒，无完整 JUnit；退出码未知 |

可直接用网页或 `uv run agent-review import examples/code_repair/correct.bundle.json --data-dir /absolute/review-data` 导入。导入这些历史记录不会重跑 Docker；要进行新实验，运行 `agent-review code-repair --candidate all`。

包内含代码快照、固定 verifier、镜像 ID、运行约束、原始日志、退出码、阶段耗时和清理记录。示例报告与源码配套；不是对用户上传报告的认证，也不能用于真实 Agent 排名。详细验收记录见 [TASK2_VALIDATION.zh-CN.md](../../validation/TASK2_VALIDATION.zh-CN.md)。
