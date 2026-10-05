# 源码评测规划与质量增强验收（2026-10-05）

## 范围与结果

- 最终后端回归：`345 passed, 2 skipped, 2 warnings`。跳过项沿用可选依赖测试；警告来自 Starlette/httpx 和 AnyIO 弃用提示。
- 新增后端测试 19 项：工具 AST 提取/边界/凭据脱敏、计划复现、独立答案、错误引用、未知工具轨迹、源码版本变化拒绝、API/CLI、并发上限、多轮串行屏障、取消与工作线程异常时证据保存、重复结果波动、部署流程计划冻结和导出。
- 独立控制程序仅从公开任务输入计算答案，不读取规则或标准答案；9 个维度、2 次重复、3 并发的正确控制为 18/18 通过。故意错误的控制全部失败；正确答案但没有工具轨迹的工具用例为 inconclusive；引用错误的检索/干扰用例失败。
- Playwright：`3 passed`。覆盖已有真实本地 HTTP 评测、原模板生成/下载，以及源码计划生成 → 2 次重复 / 3 并发 → 实际本地控制服务 → 维度质量报告 → 手机宽度 → 切换目标清空题集。
- Ruff、TypeScript/Vite 构建、Git diff 空白检查通过。
- 重建 0.3.0 wheel 和项目内 Skill 包；独立加载 wheel，验证 API、网页、既有评测、源码工具提取和计划生成通过。Skill wheel 与 runtime.json 的 SHA-256 已同步。

## 效率测量

使用 `tests/test_assessment_scheduling.py` 的 6 个独立案例，每次模拟目标调用固定等待 120 ms，运行真实调度、保存和验收流程，未调用模型。

|案例并发|总观测时间|完成与通过|
|---|---:|---:|
|1|0.891 秒|6/6|
|3|0.291 秒|6/6|

此次本机合成测量约 3.06 倍提速，不能推断真实供应商或生产环境提速。独立并发上限另用同步屏障测试，取消后未派发剩余案例；记忆、多轮、隔离保持串行顺序。

## 复现命令

```sh
uv run pytest -q
uv run ruff check src scripts tests
uv run python scripts/build_web.py
uv build --wheel
uv run python scripts/package_skill.py
uv run python scripts/check_package.py dist/agent_trace_review-0.3.0-py3-none-any.whl
```

先完成打包再验证 Skill 启动器，避免测试读取更新中的 wheel 与旧哈希。

网页测试使用独立目录和回环端口：

```sh
uv run python scripts/serve_assessment_test.py \
  --data-dir /tmp/agent-inspect-source-plan-ui --port 18767
# 另一个终端
REVIEW_TEST_URL=http://127.0.0.1:18767 npm --prefix web run test:e2e -- \
  tests/repository-planning.spec.ts tests/assessments.spec.ts
```

本轮使用本地控制 HTTP 服务和 MockTransport，未读取本地 `.env`，未发生付费模型调用，未重新拉取或构建第三方 Docker 镜像。仓库部署的计划集成用隔离的构建替身验证；既有部署能力的真实 Docker 验收记录保留在历史文档中。此处结果证明评估器的控制行为，不是第三方 Agent 的能力成绩。
