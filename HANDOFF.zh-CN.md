# Agent_Inspect 开发交接

更新日期：2026-10-03（Task 4 主动评测与服务部署完成后更新）。历史专项记录保留各自当时的验证范围，当前状态以本节及 Task 4 为准。

## 0. 当前状态

- **Task 1 已完成：** OpenCode 轨迹的三个合成场景、CLI/API、网页、报告及验证。
- **Task 2 已完成：** generic/2 证据包、固定模拟候选、实际 Docker 独立验证、CLI、网页、报告、单元测试和真实容器验收。
- **Task 3 样例阶段已完成：** 自制数字 PDF、固定独立标注、五种模拟候选及实际本地专项评估；真实转换 Agent、OCR 和官方基准尚未接入。
- **Task 4 第一版已完成：** 固定仓库能力档案、真实 HTTP 主动评测、多轮/记忆/隔离、声明与源码关联、预算曲线、版本回归、访问令牌及实际容器服务验收。
- 本目录仍**没有 `.git`**，磁盘源码是当前工作副本。GitHub 仓库已配置并发布此前实现：`git@github.com:maomaomiaomiao1234/Agent_Inspect.git`；提交操作使用独立临时克隆，迁移可克隆远端或保留当前文件。
- 最新后端测试 **266 passed / 2 warnings**；Ruff 和前端构建通过；Chrome Playwright **7 passed / 1 skipped**，另一次认证主动评测 **1 passed**。
- 网页、0.3.0 wheel 和 skill 内置引擎均已重建并验证。包版本未变，启动器以内容哈希区分不同构建，主动评测记录另有执行器源码哈希。
- 未修改原有 `.agent-review/`。实现和验证使用独立临时数据目录。

## 1. 用户目标与已确定选择

目标按阶段完成：

1. OpenCode 数据评估：从导出轨迹分析工具使用、错误恢复、证据和资源。
2. 代码修复能力评估：候选代码接受可信、固定的独立测试。
3. 文档转换能力评估：优先 PDF/扫描文档 → Markdown/JSON，暂不做 Office → PDF 排版保真。
4. 面向比赛部署的评审 Agent：已知对方开源仓库时，收集源码声明与证据，通过实际任务测量目标服务，并导出可核查报告。

用户选择先用示例跑通，再接真实 Agent。尚未提供真实被测 Agent 仓库或真实 OpenCode 导出数据。本轮没有运行真实第三方 Agent、调用付费 API 或下载完整基准数据集。

用户已同意 Task 2 使用 Docker，并授权缺少时下载官方 `python:3.12-slim`。当前本机 daemon 和镜像可用。Runner 本身不会自动下载镜像；新机器需先准备环境。

OpenCode 是过程数据来源，可与代码修复或文档转换的结果验收组合，不是互斥任务类型。按阶段推进，无需把全部模块立即部署上线。

## 2. 项目基础

Agent Trace Review，Python/FastAPI/Typer 后端、React/TypeScript/Vite 前端，包版本仍为 0.3.0。

已有：OpenCode/通用 JSON 导入、确定性诊断、Task Manifest、外部报告与 JUnit、声明式 Task Profile、跨语言 external 协议、可选 OpenAI 兼容 LLM 评审及 Inspect Scout bridge、SQLite/内容寻址工件、本地网页/CLI/报告/运行比较。

尚未接入 Harbor、SWE-bench、OmniDocBench；它们此前仅调研过。真实接入时需核对固定版本、数据格式及许可证。Docling/MarkItDown/Pandoc 可作为转换基线，不能作为 ground truth。

## 3. Task 1（保持完成）

三个场景：focused=pass、iterative=inconclusive、failed=fail。所有轨迹与验证材料均为合成，不代表真实能力。

```sh
uv run agent-review demo --scenario all
uv run agent-review list
uv run agent-review serve
```

省略 `--scenario` 仍加载旧两例。HTTP：`POST /api/demo?scenario=focused|iterative|failed|all`，要求 `X-Review-Request: 1`。Task 1 测试已包含在最新全量测试与浏览器回归中。

## 4. Task 2 实现状态

### generic/2

- `contracts.py`：GenericBundle，trace + task + 可选 diff + external verifications，报告来源与 ID 校验。
- `generic.py`：校验 diff、清除外来 evidence/event 引用、本地重建报告与执行记录证据、材料参与 run identity。
- `service.py`：正确路由通用包，并拒绝未知 generic 版本。
- `storage.py`：保留 generic/2 的 diff/报告；同目录及跨目录导出重导入 ID 与结果一致。
- `api.py`、`cli.py`：公开 generic_bundle schema。HTTP 追加 JUnit 需要明确 generic/2；裸 trace/generic/1 不会隐式升级。
- 另修复 OpenCode end_ms 的整数/浮点持久化差异：此前同一运行并发导入偶发生成两个 evaluation ID；已统一类型并加入确定性回归测试。
- 旧 generic/1 身份与行为保持兼容。导入仍只处理数据，不执行记录命令，不认证报告真实性。

### Docker runner

新增 `src/agent_trace_review/code_repair.py`、`fixtures/code_repair/session.py` 与固定 `verifier.py`。

```sh
uv run agent-review code-repair --candidate all --data-dir /absolute/review-data
uv run agent-review serve --data-dir /absolute/review-data
```

candidate 为 correct（默认）、incorrect、regression、timeout、all。结果依次为 pass、fail、fail（回归 finding）、inconclusive。

实现约束：

- 只接受上述内置候选，不支持任意命令或仓库。
- 检查 daemon/本地镜像，固定 image ID；基线与最终使用同一 ID，pull=never，不回退宿主机。
- 每阶段独立 UUID 命名容器，禁用网络、只读根目录、非 root、去 capabilities、no-new-privileges、CPU/内存/PID 限制。
- 无宿主机目录或 socket 挂载，不传入凭据；候选源码经 stdin，固定 verifier 经 Python -I -B 参数传入。
- 宿主机 10 秒执行限制；finally 清理当前容器，异常/取消同样清理；清理失败明确报错并给出容器名称。
- 保存真实退出码、日志、耗时、JUnit，验证完整三例与退出码一致；环境错误、OOM、缺测试或矛盾材料不能通过。
- Task 的 initial/final hash 来自实际代码内容映射，suite hash 来自 verifier 内容，environment hash 包含镜像及约束。
- baseline 不可用时，保留实际 final 原始记录，最终验收标为环境 error，避免仅凭 final 成功通过实验。
- framework=code-repair-fixture、demo=true。候选为模拟，测试为实际容器执行；不伪造 OpenCode 轨迹，不补造模型用量。

### 展示与文档

generic 的最终结果页依据 diff_provided 同时展示 output 与 diff；显式空 diff 显示“与基线相同”。验证页可以查看两个阶段的报告和执行记录。报告含阶段结果及最终补丁。示例说明区分候选与验证材料来源。

README、网页接入指南、`skills/opencode-trace-review/references/custom-tasks.md` 已更新。此阶段未刷新内置 wheel，后续 Task 4 已统一重建。

### 验证与材料

详见 [TASK2_VALIDATION.zh-CN.md](TASK2_VALIDATION.zh-CN.md)。

- 新增 `tests/test_generic_bundle.py` 与 `tests/test_code_repair.py`。
- 实际 Docker 验收：`uv run python scripts/check_code_repair.py --data-dir /absolute/test-data`。
- 四个已实际执行的可移植记录位于 [examples/code_repair](examples/code_repair/README.md)。导入这些文件只读取历史记录，不重跑容器。
- 实际四类结果均符合预期，回归诊断及跨目录 ID 一致性通过，本次容器均已清理。
- 后端 191 passed，2 个已有依赖弃用警告；Ruff、TypeScript/Vite 通过。
- 浏览器 5 passed，1 个原有 LLM 测试因未设置 REVIEW_MOCK_API_URL 跳过。新增测试使用实际 Docker 材料；桌面与手机截图已查看。
- 故障注入（如 OOM、daemon 异常、KeyboardInterrupt）在单元测试中验证；不要声称所有故障都实际注入过 Docker。

Task 2 临时测试服务已停止；当时的验证容器均已清理。

本轮验收临时数据：`/private/tmp/agent-review-task2-verified-20260929`，其中也有浏览器测试导入的 Task 1 等示例。不依赖该目录迁移，四个实际运行包已保存在 examples 中。

## 5. Task 3：固定文档评估样例已完成

新增 `document_evaluator.py`、`document_conversion.py`、`fixtures/document_conversion/`、`tests/test_document_conversion.py`、`scripts/build_document_fixture.py`、`scripts/check_document_conversion.py`。

```sh
uv run agent-review document-conversion --candidate all --data-dir /absolute/review-data
uv run agent-review serve --data-dir /absolute/review-data
```

五种候选：correct=pass、omitted=fail、table_error=fail、order_error=fail、missing_reference=inconclusive。

自制两页英文数字 PDF 含 10 个内容块、4 个标题、5 段正文和一张矩形表格。源 PDF 脚本不读取参考或候选；reference.json 为独立标注，已逐页看图并用提取结果核对。候选 JSON/Markdown 单独编写，错误场景只修改候选。

七项必需 external 检查：源文档/参考匹配、完整性、正文、标题层级、阅读顺序、表格、Markdown/JSON 一致性。OCR 与公式保持 unknown、默认非必需；设为必需会阻止通过。评估器读取包内固定参考，并核对导入快照及实际 PDF 字节 SHA-256；不会采用候选篡改的答案。

正文和单元格只规范化 Unicode NFC/空白，保留大小写、标点和单位。当前需要约定的块 ID，不做任意 PDF 自动对齐；Markdown 只支持 ATX 标题、段落和简单竖线矩形表格。没有覆盖 OCR、公式、合并单元格、多栏/中文文档或版式保真，不冒充 TEDS/CDM。

JSON 输出合同公开于 CLI/API schema 的 `document_output`。`document-evaluator request.json --output results.json` 支持现有 evaluator-v1 文件协议；`document-evaluate RUN_ID --data-dir ...` 显式执行固定评估器。单独导入 bundle 不执行检查，初始保持 inconclusive；重评后 run ID 和逐项结论可复现，评估版本 ID 可变化。重复相同显式评估复用已有结果。

API：`POST /api/document-demo?candidate=...`、`POST /api/runs/{id}/document-evaluation`、`GET /api/runs/{id}/document.pdf`。写请求需 `X-Review-Request: 1`。网页首页提供五例入口，详情可查看 Markdown/JSON、下载 PDF、运行检查及查看证据。不会读取内嵌材料提供的文件路径/URL 或执行任意命令。

实际本地验收已完成，保存包/请求/响应于 [examples/document_conversion](examples/document_conversion/README.md)，详细记录见 [TASK3_VALIDATION.zh-CN.md](TASK3_VALIDATION.zh-CN.md)。源码 fixture 位于 `src/agent_trace_review/fixtures/document_conversion/source.pdf`；交付副本在 `output/pdf/document-fixture.pdf`。

常规评估无额外 PDF 运行依赖。只有重建/QA 使用可选 `pdf-fixtures`（ReportLab/pdfplumber）依赖；本机无 Poppler，已用 pdfplumber/PDFium 渲染并检查两页。虚拟环境现保留 dev、scout、pdf-fixtures 三个 extra；同步时按需要显式列出，避免移除已有可选依赖。

Task 3 临时网页测试服务（18765）已停止；原有 .agent-review 未改动。

本轮 225 项后端测试通过（新增 34 项），网页 6 passed / 1 skipped；缺少 REVIEW_MOCK_API_URL 的原有 LLM 流程跳过。临时数据 `/private/tmp/agent-review-task3-verified-20260929` 同时导入了 Task 2 历史真实 Docker 包用于网页回归；本轮未重跑 Docker。

### 后续方向

三项任务的受控示例链已跑通，下一阶段应接入真实输入和执行：

1. 用户提供真实 Agent/仓库/导出后，按已有边界接入，保持任务、环境、参考与预算一致。
2. 如先接文档转换器基线，可选择明确的本地转换器和固定版本，实际运行同一 PDF，再将其输出适配为已声明的块/Markdown 格式；转换器输出不能作为标准答案。
3. 扩展真实 PDF、扫描 OCR、中文、公式等任务时先建立独立标注和明确的专项规则；当前无能力覆盖的维度继续 unknown。
4. 后续接 OmniDocBench/Harbor/SWE-bench 前核对版本、许可证、数据协议和运行范围，不能把当前简化检查叫作官方成绩。
5. 此阶段尚未刷新可分发安装包；后续 Task 4 已构建并检查 wheel、更新 skill 内置包。

尚未提供真实 Agent。不要把“样例评估完成”描述成“任意文档转换或真实 Agent 能力已经验证”。

## 5A. Task 4：主动评测与服务部署

详情见 [主动评测指南](docs/ACTIVE_ASSESSMENT.zh-CN.md) 和 [专项验收](ACTIVE_ASSESSMENT_VALIDATION.zh-CN.md)。

### 实现入口

- `repositories.py`：只读固定 Git commit 或目录内容快照，限制文件/字节/耗时，忽略密钥目录和符号链接，保存源码行号、哈希和未验证声明；不执行仓库脚本或指令。
- `assessment_contracts.py`：管理员目标表、Suite、预算、轮次、claim/source 关联、HTTP target-v1 协议。
- `target_client.py`：实际 HTTP 调用、总期限、响应上限、不跟随重定向或重试；可选固定镜像启动、健康检查和最终清理。
- `assessment_store.py`、`assessments.py`：SQLite 队列、逐案例保存、取消、重启 interrupted、固定 Profile 验收、资源曲线、声明验证、版本比较和报告/证据包。
- `api.py`、`cli.py`：主动评测及仓库接口；`AGENT_REVIEW_SERVICE_TOKEN` 保护 API、报告和工件，非回环监听必须配置令牌。
- `web/src/assessments.tsx`、`api-client.tsx`：选择目标、上传题集、扫描源码、进度/详情/比较/下载；令牌仅在页面内存中保存。
- 根 Dockerfile、`deploy/compose.yaml`：单进程评审服务及独立控制 Agent；默认通过已有 HTTP 服务发题，无需 Docker socket。

### 第一轮使用

```sh
uv run python examples/assessment/mock_agent.py --port 9081
# 另一终端
uv run agent-review serve --data-dir ./review-data \
  --targets examples/assessment/targets.json --port 8765
```

打开 `http://127.0.0.1:8765/#/assessments`，选择 control 并上传 `examples/assessment/suite.json`。管理员登记真实目标的 endpoint、协议路径、凭据环境变量名称和固定仓库版本；客户端不能提交任意目标地址或部署命令。

服务使用单进程内的两个 worker，排队与执行总数最多 32；不要配置多个 Uvicorn worker 或并发服务共用 data-dir。题集冻结，标准答案留在评审端；HTTP 只观察外部对话，内部轨迹覆盖保持 partial。预算缺少用量时保持 unknown，不能强制供应商账单。取消保留部分结果，重启不会自动重放任务。

### 实际验收

五种实际 HTTP 控制程序结果：correct 16 pass；incorrect/noop 各 16 fail；missing 16 inconclusive；leaky 12 pass / 4 fail（隔离）。真实 Docker 目标 16 pass；评审服务和控制目标分开的容器部署 16 pass，认证、只读 Git 扫描、报告/工件下载和重启持久化通过。

证据归档在 `examples/assessment/verified/`，包含六类运行 bundle、完整服务部署记录与哈希摘要。只读这些文件不重跑 Agent。目前仅支持导入其中的 generic 运行包，未实现整个主动评测 bundle 的导入入口。

本机平台 linux/arm64；评审镜像 ID `sha256:de095cd30858b7177f2113cd0e9d90f4ef8f491da61d767b5fef250256064b14`，目标镜像 ID `sha256:17e8beaddd7e1ea49d1e612a20be059cf4eea9c7bd9408db957c6aa91f1778b8`。测试创建的容器、网络、数据卷及临时网页服务已清理。原 `.agent-review` 未改动。

### 接续范围

第一版服务已具备完整受控评测闭环。下一阶段根据真实比赛协议接入参赛 Agent：目前尚无真实目标、完整 A2A、源码自动构建与可信镜像绑定、官方基准评分、通用工具故障注入、分布式队列或统计置信区间。源码关联不是因果证明，控制样例不是比赛成绩。

## 6. 环境与复现

建议 Python 3.12（项目要求 3.11+）、uv、Node.js 20+、npm、Docker Desktop/Engine。端到端测试使用 Chrome。

```sh
uv sync --python 3.12 --extra dev --extra scout --extra pdf-fixtures
npm --prefix web ci
uv run pytest -q
uv run ruff check src scripts tests
uv run python scripts/build_web.py
```

Docker Server 29.8.1，本机 linux/arm64 镜像 ID：
`sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`。

新机先执行 `docker version`、`docker image inspect python:3.12-slim`；镜像缺少时显式拉取适合本机架构的版本，记录其实际 ID。

完整 UI 复现：

```sh
uv run python scripts/check_code_repair.py --data-dir /absolute/test-data
uv run agent-review serve --data-dir /absolute/test-data --port 18765
# 另一终端
REVIEW_TEST_URL=http://127.0.0.1:18765 REVIEW_CODE_REPAIR_FIXTURES=1 npm --prefix web run test:e2e
```

服务、导入、实验和报告命令须使用同一绝对 data-dir。浏览器 http://127.0.0.1:18765。写 API 要求 `X-Review-Request: 1`。

## 7. 迁移与分发

必须保留当前 src、tests、scripts、examples、web/src、web/tests、配置/锁文件、skills 和文档。不要重新下载旧版本覆盖。

`.venv`、node_modules、缓存、web/dist、static 可以在新机重建。`dist/` 与 skill 内置 wheel 已刷新；后续改代码时重新执行 build_web、uv build、check_package、package_skill。原 `.agent-review` 可选迁移，须关闭服务后把数据库与 artifacts 完整备份；不要只复制 SQLite。

不要迁移本机凭据、Token、`.env*` 或 CLI 登录状态到公开归档。用户已授权提交到 GitHub 的 Agent_Inspect 仓库，原始本地目录没有 `.git`，提交可使用独立克隆。

## 8. 下一会话接续指令

```text
阅读 HANDOFF.zh-CN.md、docs/ACTIVE_ASSESSMENT.zh-CN.md 和 ACTIVE_ASSESSMENT_VALIDATION.zh-CN.md。
Task 1/2/3 受控示例及 Task 4 第一版主动评测服务已完成。
当前目录没有 .git；GitHub 已发布此前实现，提交使用独立克隆，以本地当前源码核对远端状态。
下一阶段按比赛要求适配真实 Agent、独立题集或构建来源；真实 Agent 尚未提供。
复用现有 trace/Profile/external 协议；区分模拟候选、实际验证和未覆盖维度。
当前文档评估器只支持固定两页数字 PDF 和约定块 ID；不支持任意 PDF 自动对齐、OCR 或公式。
不把转换器输出当作 ground truth，不把简化指标称为官方 TEDS/CDM。
主动评测使用管理员目标表、固定题集和单进程服务，不认证源码到镜像来源或强制供应商费用。
测试使用独立 data-dir，避免修改原 .agent-review。wheel 与 skill 已重建，修改后须再次刷新。
```
