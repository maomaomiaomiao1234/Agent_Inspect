# 主动评测范围与报告改进验收

日期：2026-10-05。验证对象为本次工作区代码与重新构建的 0.3.0 wheel。

## 目标与证据

|目标|实现|验证证据|
|---|---|---|
|提升派发任务与独立验收|网页默认通用模板，CLI/API 支持 `general`、重复和并发；标准答案留在评审端|`test_general_api_and_cli_preserve_repeats_and_do_not_schedule`；请求字段白名单断言；真实 HTTP 浏览器流程|
|扩充评测范围|12 类任务 / 7 个维度，包括多轮澄清纠错、空结果、冲突引用、多语言数据|3 个种子分别执行 30 案例 × 3 次重复；独立控制程序只根据公开输入计算答案，270 次均通过|
|提升判定质量|必需字段存在检查、首轮和末轮检查、精确引用检查；执行错误保留为证据不足|空答和错误答案全部未通过；正确事实配伪造引用未通过；首轮错误末轮正确仍未通过；超时均为证据不足；记忆首轮缺字段被拦下|
|提升结果可读性|结论摘要、四类计数、下一步、覆盖与漏测、问题筛选、期望/实际对照、可折叠资源明细|浏览器筛选/搜索/证据链接/Markdown 下载断言；桌面和 390px 手机截图人工核对|
|分发可用|刷新静态网页、wheel、skill 内置 wheel 与校验清单|脱离源码导入 wheel 的 API/UI/模板/评测校验通过；skill 启动器完整性测试通过|

## 执行结果

- 后端全量回归：`python -m pytest -q`，**431 passed，2 skipped**。跳过的是可选 `pdfplumber` 源 PDF 检查和 `inspect_scout` 集成。
- 随后补充两项记忆首轮漏字段回归：`python -m pytest tests/test_suite_generation.py tests/test_repository_planning.py -q`，**25 passed**，包含两项新增检查；未再重复全量运行。
- 受影响的浏览器流程：`assessments.spec.ts`、`general-assessment.spec.ts`、`repository-planning.spec.ts`、`usage.spec.ts`，**7 passed**。后端使用独立临时数据目录和回环测试服务。
- `python -m ruff check src scripts tests`、`git diff --check`：通过。
- `python scripts/build_web.py`：TypeScript/Vite 构建并复制静态资源成功。
- `uv build --wheel`、`python scripts/check_package.py dist/agent_trace_review-0.3.0-py3-none-any.whl`：通过；包内通用题集和报告摘要可用，空答控制 24 次全部未通过。
- `python scripts/package_skill.py`：成功；`python -m pytest tests/test_skill_launcher.py -q`：**6 passed**。

最终 wheel SHA-256：`9a87e8c935f1d9db0ba0f0a23c9491de5da2898ac0076f677271e976bfd15708`。

## 使用与限制

重启更新后的服务，在「主动评测 → 新建评测」生成通用任务即可使用。详细操作见[通用评测与结果阅读](docs/GENERAL_ASSESSMENT.zh-CN.md)。历史数据保留，新摘要和逐项对照由重新运行的评测产生。

验证使用本地控制 Agent 与内存 HTTP transport，未调用真实模型、部署外部仓库或测量生产能力。新增模板不覆盖持久记忆、会话隔离或真实工具执行；这些维度继续使用原有专项模板与独立题集。报告不会把未测维度视为失败，也不提供总体能力分数。
