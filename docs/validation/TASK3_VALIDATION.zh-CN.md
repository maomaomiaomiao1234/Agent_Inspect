# Task 3 验证记录

日期：2026-09-29。已完成固定 PDF → Markdown/JSON 的**样例评估链**；未接真实转换 Agent、OCR、模型 API 或 OmniDocBench。

## 源文档与独立参考

- 源文档：`src/agent_trace_review/fixtures/document_conversion/source.pdf`，可交付副本 `output/pdf/document-fixture.pdf`。
- 两页英文数字 PDF，10 个内容块：4 个标题、5 段正文、1 张 5×3 矩形表格。页脚和页码排除在内容验收之外。
- PDF SHA-256：`66db46fd7e9b8b77036c3c7767a6099185d548ed2c89530c1889e817dd6ce2e8`。
- `scripts/build_document_fixture.py` 独立编排 PDF，不读取 reference/candidate 文件。
- `reference.json` 为独立标注；已查看两页渲染，核对标题、行文顺序、表格数值与段落。另用 pdfplumber 提取文本/表格与标注核对。
- 源 PDF 与标注固定后，模拟候选来自单独编写的 `correct.json` / `correct.md`。错误注入只修改候选，不修改标准答案。

生成环境：ReportLab 4.5.1；提取与渲染：pdfplumber 0.11.10 / PDFium（pypdfium2 5.13.0）。本机无 Poppler，使用 pdfplumber 的 PDFium 渲染路径完成逐页视觉检查。开发依赖记录于可选 `pdf-fixtures` extra；常规验收不加载这些库。

## 实際执行的本地评估

运行 `scripts/check_document_conversion.py`，实际调用固定评估器并通过现有 evaluator-v1 / Profile 流程保存结果。候选不是真实转换器生成。

| 候选 | 结果 | 必需检查通过 / 失败 / 未知 | Run ID |
| --- | --- | --- | --- |
| correct | pass | 7 / 0 / 0 | run_2d24edc5c7afeac2300f |
| omitted | fail | 4 / 3 / 0 | run_ecf4fa73d6a6b031dfe9 |
| table_error | fail | 6 / 1 / 0 | run_cb39616a1de9cf217f3d |
| order_error | fail | 6 / 1 / 0 | run_cdff19623d4ef3b8417f |
| missing_reference | inconclusive | 0 / 0 / 7 | run_79f01ca43bc58d16ed5d |

所有场景的两个可选检查（OCR、公式）均为 unknown。没有将未实现的维度判为通过。Token、费用和 Agent 耗时未观测，保持未知。

实际定位：

- omitted：缺少 `limitation` 正文块，正文、完整性和完整阅读顺序检查失败。
- table_error：Tuesday/Sprinkler 的 `17` 改成 `71`，报告定位 `water-table 行3列3`（行数含表头）。JSON 和 Markdown 都包含错误，因此一致性可通过、独立表格验收失败。
- order_error：method 与 limitation 互换，正文内容相同但阅读顺序失败。
- missing_reference：源 PDF 存在但参考快照缺失，必需检查保持 unknown。

包、请求和响应保存在 [examples/document_conversion](../examples/document_conversion/README.md)。验收脚本验证了源 PDF 字节、报告规则数、未知维度、证据绑定及跨目录往返：run ID 相同，重导入初始结论 inconclusive，显式重评后逐项结果恢复。标准 bundle 不隐式携带/信任评估历史。

## 测试和浏览器检查

- `uv run pytest -q`：**225 passed**，2 个已有 FastAPI/Starlette 依赖弃用警告。
- 新增文档模块测试：34 项，通过。包含可选 PDF QA，本轮已实际执行，没有跳过。
- `uv run ruff check src scripts tests`：通过。
- `uv run python scripts/build_web.py`：TypeScript/Vite 通过，静态网页已刷新。
- Chrome Playwright：**6 passed / 1 skipped**。跳过原有 LLM 评审流程，原因是没有配置 `REVIEW_MOCK_API_URL`。
- 新文档浏览器测试验证五种结果、Markdown/JSON、PDF 下载、单元格证据、重新检查、unknown 维度和接入指南。
- 已查看桌面与 390 px 手机截图；页面无横向溢出、遮挡或文字重叠。
- Task 1/2 的原有后端和浏览器流程继续通过。Task 2 浏览器使用上一阶段真实 Docker 导出的记录，本轮没有重跑或声称重跑容器。

关键后端边界：篡改参考并同步篡改候选不能通过；缺源文件、base64/哈希错误、不同 PDF、扫描类型保持未知；重复 ID、非矩形表格、遗漏/多余/空块、标题层级、页码、单位、标点和仅 Markdown 错误均被发现；缺 Markdown 保持未知；将 OCR/公式/未实现规则设为必需会阻止通过。输入/规则哈希不匹配的 external 请求或响应被拒绝。未下载或执行候选提供的路径、URL 或命令。

## 复现

```sh
uv run agent-review document-conversion --candidate all --data-dir /absolute/review-data
uv run python scripts/check_document_conversion.py --data-dir /absolute/test-data
uv run agent-review serve --data-dir /absolute/review-data --port 18765
# 另一终端（不要求 Docker）
REVIEW_TEST_URL=http://127.0.0.1:18765 npm --prefix web run test:e2e
```

只有开发时重建 PDF/检查其内容需要：

```sh
uv sync --extra dev --extra pdf-fixtures
uv run --extra pdf-fixtures python scripts/build_document_fixture.py
uv run --extra pdf-fixtures pytest -q tests/test_document_conversion.py
```

已使用 Scout 的环境同步时加 `--extra scout`。更新源 PDF 后必须重新审查标注和视觉效果，再显式调整 source hash/version；评估器不会自动接受变化的 PDF。

独立终端命令 `document-evaluator` 已实际执行，输出与保存的 correct.results.json 完全一致。临时测试服务（18765）已停止。

本轮临时数据：`/private/tmp/agent-review-task3-verified-20260929`。未修改原有 `.agent-review/`。交付不依赖临时目录，样例材料已经导出到项目中。

## 限制与后续

- 当前为固定样例、约定块 ID 和受限 Markdown 格式的验收器，不是任意 PDF 的解析器或自动对齐器。
- 没有覆盖中文、扫描 OCR、公式、合并单元格、多栏文档或版式保真，也不宣称官方 TEDS/CDM。
- 数字 PDF 的生成与 QA、固定内容检查已实际执行；Markdown/JSON 候选仍为模拟输出。真实转换器基线和 Agent 适配需后续接入。
- 来源哈希绑定内容，不提供第三方报告真实性认证。
- `dist/` 和 skill 内置 wheel 未重新打包；使用项目源码 `uv run`。
