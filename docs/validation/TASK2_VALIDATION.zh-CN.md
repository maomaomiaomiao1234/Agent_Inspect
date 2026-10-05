# Task 2 验证记录

日期：2026-09-29。范围：内置候选模拟 + 实际 Docker 独立验证。没有调用真实 Agent、付费模型、Harbor 或 SWE-bench。

## 实际容器结果

使用 Docker Server 29.8.1，linux/arm64 镜像 `python:3.12-slim`，解析并固定 image ID：

`sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`

固定 verifier SHA-256：`04c2271a3ac91e2bf0a2615e72670e2316ed0c40df4654e71af987f81465d733`。

基线内容映射哈希：`deb5a803db58065feedb1c5e1777108871d81384388fef3fdf0953195d696721`。

每次实验的 baseline 都执行三个测试：过期前与过期后通过，刚好到期失败，容器退出码 1。

| 候选 | 最终结果 | 最终通过/失败 | 最终退出码 | 最终阶段耗时 | Run ID |
| --- | --- | --- | --- | --- | --- |
| correct | pass | 3 / 0 | 0 | 259.512 ms | run_ab92977682305048bbbd |
| incorrect | fail | 2 / 1 | 1 | 243.007 ms | run_a46ef3fad443b1244493 |
| regression | fail | 2 / 1 | 1 | 249.264 ms | run_c7da19a395e1489cea88 |
| timeout | inconclusive | 未完整执行 | 未知 | 10096.204 ms | run_9070c986668c39921ee2 |

阶段耗时是本机本轮观测，包含创建、执行、状态检查和清理，不是 Agent 性能指标。timeout 由宿主机 10 秒限制触发，保留部分 stderr，未伪造 JUnit 或退出码。

regression 的 `SessionExpiry::test_before_expiry` 从基线 pass 变成 final fail；已有分析器产生 `test_regression` finding 并引用两个阶段的报告。

实际运行 `scripts/check_code_repair.py` 验证四种结果、完整 cases、同一镜像、未知 Token/费用、跨目录 bundle 往返的 run/evaluation ID 一致，并确认本次创建的八个容器均已删除。另一次 CLI `--candidate all` 冒烟运行得到相同四种结果。

真实材料已保存至 [examples/code_repair](../examples/code_repair/README.md)，供后续无需 Docker 导入查看；源码最新报告校验逻辑已再次验证这些已保存的 JUnit。

## 自动检查

- 后端：`uv run pytest -q`，191 passed，2 个已有 FastAPI/Starlette 依赖弃用警告。
- 静态检查：`uv run ruff check src scripts tests`，通过。
- 前端：`uv run python scripts/build_web.py`，TypeScript/Vite 通过，静态资源已刷新。
- Chrome Playwright：5 passed，1 skipped。跳过原有 LLM 评审用例，原因是未提供 `REVIEW_MOCK_API_URL`。
- 新网页测试直接使用本轮真实容器材料，验证四类结果、代码 diff/空 diff、baseline/final、报告与执行记录、回归证据和报告导出。
- 已查看桌面和 390 px 手机截图；手机页面无横向溢出。

新增单元测试覆盖 generic/1 旧 identity、generic/2 同目录与跨目录往返、材料变化、外来 evidence ID 清除、报告计数/来源/重复 ID、非法 diff、缺最终状态/0 用例/超时，以及 HTTP JUnit 明确版本和 schema。

Runner 单元测试使用 mock Docker 检查隔离参数、源代码 stdin、固定 verifier 参数、缺 daemon/镜像、不下载/不回退宿主机、报告不完整或退出码矛盾、OOM、创建失败、状态读取失败、客户端异常、超时、KeyboardInterrupt、清理失败。异常路径通过单元测试，四种正常预期场景和超时另有实际容器验证；未宣称所有 daemon 故障都在真实 Docker 中注入过。

最终回归发现并修复一个既有并发导入问题：OpenCode 的 end_ms 赋值绕过字段转换，首次分析为整数、数据库读回后为浮点数，导致 corpus/evaluation 哈希不同。已统一为浮点数并新增持久化前后哈希一致性测试；全量测试重新通过。未改动用户既有数据库。

## 复现

```sh
uv run python scripts/check_code_repair.py --data-dir /absolute/test-data
uv run agent-review serve --data-dir /absolute/test-data --port 18765
# 在另一终端执行
REVIEW_TEST_URL=http://127.0.0.1:18765 REVIEW_CODE_REPAIR_FIXTURES=1 npm --prefix web run test:e2e
```

本轮临时数据目录：`/private/tmp/agent-review-task2-verified-20260929`。该目录也包含浏览器回归导入的其他示例。未修改原有 `.agent-review/`。

## 交付边界

- 容器只验证固定内置候选，不接受任意仓库或命令；没有实际 Agent 生成补丁。
- 导入报告不执行命令，不提供报告真实性认证。
- 每次新实验有新 run ID；重复导入同一包幂等。
- `dist/` 和 skill 内置 wheel 保持旧版，本轮没有重新打包或安装到个人 skills。
- Task 3（PDF → Markdown/JSON）尚未实现。
