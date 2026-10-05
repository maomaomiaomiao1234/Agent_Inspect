# Agent_Inspect 开发交接

## 生成不设 Token 上限并开启思考（2026-10-05）

按用户最新要求，适配器生成配置独立覆盖 `ReviewConfig` 的输出与超时字段。未设置/留空/auto 的 `AGENT_REVIEW_ADAPTATION_MAX_OUTPUT_TOKENS` 解析为 None，真实请求完全省略 `max_tokens` 和 `max_completion_tokens`；正整数仍作为显式可选限制，不再继承共享评审配置的 32768 上限。DeepSeek 默认启用 thinking，其他供应商未显式设置时不发送扩展参数。本机 `.env` 已修改为留空输出上限、thinking=enabled、timeout=900，仅调整这些生成参数，凭据未输出且 `.env` 保持 Git 忽略。

生成默认超时 900 秒，可配置至 3600；响应内存保护从 512 KiB 提高至 8 MiB，以容纳长思考输出，保留取消和结构验证。每轮元数据标记 output_limit_source=provider_default/configured，供应商自己的默认输出/上下文边界仍生效；length 仍不以相同设置自动修复。网页展示实际生成上限及思考模式，README、指南和 `.env.example` 同步。上节 disabled/16384 的建议是此前截断诊断阶段的历史记录。

验收：420 passed、2 skipped；Ruff/diff 检查及前端构建通过，长思考响应、响应内存保护、两种 Token 参数省略和配置回退均已覆盖。本地实际 `.env` 经模拟传输确认不发送 Token 上限且 thinking=enabled；没有新增付费 API 调用。用户需要重启当前服务并提交新任务后实际验证陌生仓库。

## DeepSeek 自动接入生成截断诊断（2026-10-05）

用户首次陌生仓库测试 `repository_job_be7c1d2d32094a868a546a90d2b14319` 在生成阶段失败，两轮分别用满 8192 输出 Token，合计输入 20382、输出 16384、总计 36766，尚未进入接入验证。实际 `.env` URL/模型格式正确，生成选 LLM 组、被测选有密钥的 SMOL 组；没有修改本地 `.env` 或重新调用付费 API。旧记录没有保存供应商结束原因与思考用量，无法补回。

新增可选 `AGENT_REVIEW_ADAPTATION_THINKING=enabled/disabled`，留空不发送扩展参数，仅作用于生成适配器。DeepSeek 推荐 disabled、生成输出 16384、超时 180，修改后重启源码服务。每轮保存受限结束原因、输出上限、思考设置与正文长度；返回的思考 Token 单独统计，包含在输出 Token 内，不重复加入总数。`finish_reason=length` 立即停止，保留用量，不以相同预算修复。操作见自动适配指南及 `.env.example`。相关回归 61 passed，Ruff 和 diff 检查通过；尚未用用户配置重跑真实仓库。

## Python LLM 自动适配（2026-10-05）

用户授权实施未知仓库的自动接入。`recipe=auto` 优先清单与 smolagents 固定配方，然后对 Python 仓库调用服务端 LLM；也支持显式 `recipe=llm`。新增 `repository_adaptation.py` 和评审器固定的 `repository_templates/auto_runtime.py`，生成有界桥接代码及部署文件，宿主机仅静态检查，实际代码在 Docker 内运行。

先做独立接入检查，要求匹配固定源文件/符号/哈希的原入口被观察；不通过则有限修复（默认一次、最多两次）。正式任务同样校验入口证据，不能把未观察原入口的答案判为能力通过。验证证据来自容器自报，不是防篡改认证，也不保证完整工作流未被旁路。未知外部服务、依赖或不能可靠确定入口时保存失败/不支持原因。

自动接入无上传题集时启用现有源码规划，答案由评审端程序生成；LLM 和目标不接收独立 Profile/标准答案。生成 Token、接入检查 Token 和正式评测分别保存，失败/截断保留可见用量。固定模板观察配置模型端点的 httpx 非流式请求、支持显式原生模型/工具包装，默认 partial，未观测用量保持未知。协议增加可选 `adapter_evidence`，原协议继续兼容。

网页可调整是否自动接入和修复次数，展示生成/修复/验证阶段及分开用量。完整导出保留材料、每轮生成文件/诊断/验证任务；新增 `GET .../adapter-files` 下载文件 ZIP，不含原源码或 `.env`。CLI 增加 `--auto-adapt/--no-auto-adapt`、`--adaptation-repairs`。服务端配置见 `.env.example`，操作见 [自动适配指南](docs/AUTO_ADAPTATION.zh-CN.md)。

实际 Docker 验收成功：本地合成生成 API + 合成被测模型 + 本地固定的合成 Python 仓库，1 次生成、1 次接入检查和 1 次正式调用，观察到原入口、calculator 与 ≥15 Token，正式单题 pass，资源清理 completed。验证不是陌生真实仓库适配成功率，也不是供应商模型能力成绩。本轮没有读取/修改本地 `.env` 或新增付费调用。详细记录见 [验收](AUTO_ADAPTATION_VALIDATION.zh-CN.md)；后文“其他仓库必须人工添加清单”为历史状态。

## 网页真实模型评测入口（2026-10-05）

用户要求启动网页后只填仓库链接，模型配置从 `.env` 读取，其余评测参数在网页调整。现已实现：`uv run agent-review serve` 自动加载工作目录 `.env`，进程环境优先，本地监听默认启用仓库构建；网页默认真实模型，缺配置时阻止提交并说明缺失变量，不静默切换离线。

模型配置按完整组选择：专用 `AGENT_REVIEW_TARGET_*`、带密钥的 `SMOL_MODEL_*`、带密钥的 `AGENT_REVIEW_LLM_*`。不会混用不同组的地址/模型/密钥；浏览器只接收状态、地址和模型名。smolagents 自动注入配置；其他仓库清单声明的标准模型变量也可自动映射，额外变量仍由服务端白名单控制。密钥仅用于运行容器，不进入源码或构建上下文。

网页可调整模式、案例数、种子、版本、单题期限、输出 Token 上限、重复次数、并发及生成策略。上传固定题集时保留其参数。源码规划题集和导出包保存实际执行参数；累计期限仍限制为 900 秒。自动部署配方仍仅 smolagents，其他仓库需要部署清单和 `target-v1` 适配器。

验收：后端 383 passed / 2 skipped，Chrome 9 passed，Ruff 和前端构建通过。使用合成模型配置、模拟任务管线和本地浏览器检查；未读取或修改本地 `.env`，未新增供应商调用。当前使用说明见 [仓库评测指南](docs/REPOSITORY_ASSESSMENT.zh-CN.md)，详细范围见 [网页入口验收](WEB_MODEL_ASSESSMENT_VALIDATION.zh-CN.md)。后文默认离线的记录为此前行为；CLI `assess-repo` 保留原有默认，网页显式选择自动模式并默认解析为真实模型。

## Token 统计第一阶段（2026-10-05）

按用户“先探索再决定”的要求完成诊断，随后授权实施第一阶段。现场最近任务实际为 offline，并非真实模型用量被丢失；已有 DeepSeek 历史单题记录 3166/99/3265。修复现有统计：逐字段完整性与原因、完整调用补齐汇总、错误/取消/超时保留可见下界、显式资源摘要避免伪造 llm 事件及重复计数。案例、预算、按模型、运行详情、Profile、页面和导出使用同一证据；部分用量超上限可判失败，未超出仍未知。

目标响应新增可选 `usage_mode`，通用轨迹新增可选 `usage_summary`，Metric 增加 `not_applicable`。原有完整总量字段继续仅保存完整数值，下界放 `usage.fields.*.value`；运行前健康诊断入口 `/api/targets/{id}/telemetry-readiness` 不调用任务、不启动容器。smolagents 健康接口声明模式与采集能力；默认离线模式不会因为配置密钥自动变为真实模型。

内置自动仓库配方仍只有 smolagents，其他仓库需部署清单、`target-v1` 或通用轨迹转换。下一阶段是通用 SDK/供应商用量采集，之后试点受控模型网关；尚未实施。旧任务和旧评估版本不自动改写，缺失的历史用量无法恢复。说明见 [探索方案](docs/TOKEN_USAGE_EXPLORATION.zh-CN.md)、[调用记录指南](docs/TARGET_TELEMETRY.zh-CN.md)。

验收见 [TOKEN_ACCOUNTING_VALIDATION.zh-CN.md](TOKEN_ACCOUNTING_VALIDATION.zh-CN.md)：后端 366 passed/2 skipped，smolagents 适配器 11 passed，Chrome 7 passed，Ruff/前端/wheel/Skill 通过。恢复了被忽略的 smolagents 独立环境（离线 uv 缓存），没有读取/修改 `.env`，没有新增供应商调用，临时测试服务已关闭。

## 已知源码评测增强（2026-10-05）

新增 `source_tools.py`、`repository_planning.py`、`assessment_quality.py`。源码档案含 Python 工具候选和能力线索；`plan-repository` CLI、`/api/assessment-suites/plan/{repository_id}`、网页源码模板输出绑定 source hash 的受控题集和漏测项。仓库自动部署通过 `planning` / `--source-plan` 启用，导出包保存完整计划，固定 checkout 档案只扫描一次。

Suite 增加 `concurrency`（默认 1，上限 4）和可选 `repository_source_hash`；独立案例并发，多轮/记忆/隔离案例串行屏障，取消保留已开始的结果。报告新增维度通过率范围、分位耗时、重复稳定性、未知证据计数和源码工具/声明覆盖。通过率范围不是置信区间；静态候选和通用题不认证完整能力声明。

使用和边界见 [docs/SOURCE_GUIDED_ASSESSMENT.zh-CN.md](docs/SOURCE_GUIDED_ASSESSMENT.zh-CN.md)。后文“尚未根据仓库生成题目”的描述是旧阶段记录；本轮实现确定性受控规划，仍未提供任意业务工具的可靠答案自动推断、官方基准成绩或通用零配置部署。

验收见 [SOURCE_GUIDED_VALIDATION.zh-CN.md](SOURCE_GUIDED_VALIDATION.zh-CN.md)：345 passed、2 skipped；Chrome 3 passed，含新流程与移动宽度；wheel/Skill 已刷新。未发生付费调用，本地 `.env` 未读取或改动。

更新日期：2026-10-04（仓库自动拉取、部署、评测首版完成）。历史专项记录保留各自当时的验证范围，当前状态以本节及 Task 4 为准。

本轮新增：主动评测模型/工具遥测已接入，包括逐次 Token、参数/结果、状态、耗时、任务关联、失败保存、统计和网页展示。根目录 `.env` 实际配置及 DeepSeek `/models` 已验证，真实单题 1/1 pass，2 次模型请求、calculator/final_answer 两次工具入口、3265 Token；没有凭据泄露。接入指南见 [TARGET_TELEMETRY](docs/TARGET_TELEMETRY.zh-CN.md)，验证及运行 ID 见 [验收记录](TARGET_TELEMETRY_VALIDATION.zh-CN.md)。启动前重启旧版目标与评审服务，新任务才会采集内部记录。

## 0. 当前状态

- **Task 1 已完成：** OpenCode 轨迹的三个合成场景、CLI/API、网页、报告及验证。
- **Task 2 已完成：** generic/2 证据包、固定模拟候选、实际 Docker 独立验证、CLI、网页、报告、单元测试和真实容器验收。
- **Task 3 样例阶段已完成：** 自制数字 PDF、固定独立标注、五种模拟候选及实际本地专项评估；真实转换 Agent、OCR 和官方基准尚未接入。
- **Task 4 第一版已完成：** 固定仓库能力档案、真实 HTTP 主动评测、多轮/记忆/隔离、声明与源码关联、预算曲线、版本回归、访问令牌及实际容器服务验收。
- **smolagents 接入及首次真实模型测试已完成：** 独立依赖、真实 ToolCallingAgent、HTTP 适配器、6 项固定题集、单案例题集及 DeepSeek 模型入口。脚本模型的 HTTP 校准 6/6 pass，适配层最新 8 项测试通过；用户配置密钥后真实 deepseek-flash 单题 1/1、完整题集 6/6 pass。
- **自动生成题集已完成：** smolagents 模板按种子生成 1–30 个案例、程序标准答案与 Profile；网页可预览/下载并直接评测，CLI/API 同样可生成。最大 30 题的真实框架 offline 校准通过；未新增 DeepSeek 调用。
- **仓库自动部署评测首版已完成：** CLI/API/网页接收公开 GitHub HTTPS URL，固定提交，按内置 smolagents 配方或 `agent-review.json` 构建实际源码，健康检查后执行独立题集，导出构建和评测证据并清理资源。实际仅提供 smolagents URL 的默认流程为 12/12 pass，offline/demo；不代表模型能力成绩。操作与边界见 [指南](docs/REPOSITORY_ASSESSMENT.zh-CN.md)，真实构建、崩溃恢复与回归证据见 [验收记录](REPOSITORY_ASSESSMENT_VALIDATION.zh-CN.md)。
- **此前审核的三个问题已修复：** 容器身份与临时环境文件持久化并在重启时回收；多轮历史接受 JSON 输出；用量不完整时仍扣除已知 Token 并判定已知超额。构建子进程随父进程异常退出而终止，同一数据目录禁止并发启动两个服务。
- 当前目录已有 `.git`，本轮实现基线为远端 main `d51fb151a57356ff857a127ae37b5719a2f1e473`。仓库：`git@github.com:maomaomiaomiao1234/Agent_Inspect.git`。
- 本轮后端 **326 passed / 2 skipped / 2 warnings**（可选 PDF/Scout 检查跳过），smolagents 独立适配层 **11 passed**；Ruff 和前端构建通过；Chrome 主动评测、生成下载和遥测 **3 passed**。此前仓库入口、真实 Docker 和认证浏览器验证保留在各自记录中。
- 网页、0.3.0 wheel 和 skill 内置引擎均已重建并验证。包版本未变，启动器以内容哈希区分不同构建，主动评测记录另有执行器源码哈希。
- 未修改原有 `.agent-review/`。实现和验证使用独立临时数据目录。

## 1. 用户目标与已确定选择

目标按阶段完成：

1. OpenCode 数据评估：从导出轨迹分析工具使用、错误恢复、证据和资源。
2. 代码修复能力评估：候选代码接受可信、固定的独立测试。
3. 文档转换能力评估：优先 PDF/扫描文档 → Markdown/JSON，暂不做 Office → PDF 排版保真。
4. 面向比赛部署的评审 Agent：已知对方开源仓库时，收集源码声明与证据，通过实际任务测量目标服务，并导出可核查报告。

用户选择先用示例跑通，再接真实 Agent。现已选用 smolagents 1.26.0，并完成脚本模型接入校准及真实模型测试。用户指定 DeepSeek，地址为 `https://api.deepseek.com`，模型为 `deepseek-flash`；已在本机 `.env` 填写密钥并授权启动测试。两次真实评测均通过，费用未返回。未提供真实 OpenCode 导出数据，未下载完整基准数据集。

用户已同意使用 Docker，当前本机 daemon 和镜像可用。固定镜像 Runner 不自动下载镜像；新的仓库构建流程会按 Dockerfile 下载基础镜像和依赖，新机器需先准备 Git 与 Docker。

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

第一版服务已具备完整受控评测闭环，受支持仓库可以自动构建并记录本机观察到的源码/镜像绑定。下一阶段根据真实比赛协议接入参赛 Agent：目前尚无真实参赛目标、完整 A2A、第三方构建签名认证、官方基准评分、通用工具故障注入、分布式队列或统计置信区间。私有仓库、任意仓库的零配置适配及恶意多租户构建隔离尚未支持。smolagents 起步目标及真实模型测试见下文。源码关联不是因果证明，控制样例不是比赛成绩。

### smolagents 起步目标（2026-10-04）

操作见 [examples/smolagents/README.md](examples/smolagents/README.md)，验证及归档见 [examples/smolagents/VALIDATION.zh-CN.md](examples/smolagents/VALIDATION.zh-CN.md)。本例固定 smolagents `1.26.0`，上游提交 `12c1bc820eca50ace6f80a21d90426d41d74f845`；源码在忽略目录 `tmp/third-party/smolagents`，依赖使用独立 `.venv` 和锁文件。

`agent_server.py` 提供真实 `ToolCallingAgent` 与三个固定工具，支持 `offline`/`openai` 两种后端。登记表检查健康响应的 backend，避免将脚本模型作为真实模型成绩；固定题集验证算术、两步调用、多轮记忆、隔离及公开输入干扰。用量按本轮模型调用统计，内部工具材料标记目标自报，HTTP 轨迹保持 partial。

已实际完成本地 HTTP 校准 `assessment_2dd4b8cc8282458a82831e0433280ab2`，6/6 pass、demo=true；当时适配层测试 7 passed。后续 DeepSeek 参数更新及 SDK 协议测试通过后，最新为 8 passed、Ruff 通过。官方 DeepSeek 域名显式关闭 thinking，兼容当前框架的 `tool_choice=required`；其他提供商不受影响。原 `.agent-review` 未修改。此次仅新增独立示例与文档，核心 wheel/skill 未重建。

用户随后配置本机密钥并授权启动测试，实际 deepseek-flash 单题 `assessment_3a5cbb9a7bb74143ab4e555553763c22` 为 1/1 pass，完整题集 `assessment_5b27fb7af4ba4ba9a69d373d7e714515` 为 6/6 pass；两者 demo=false。完整评测自报 24898 Token，费用 unknown。报告在 `review-data-smolagents/reports/`，数据库与工件使用同一个绝对 data-dir；该目录已忽略，六份导出均检查没有 API 密钥回显。

当前服务按用户要求保持运行：目标 PID 22789/9091（exec session 1529），评审为更新后的 PID 23949/8765（exec session 27912）；旧评审 PID 22792 已正常结束。网页 `http://127.0.0.1:8765/#/assessments`。重新启动前先检查端口与进程，不要重复启动。停止时仅结束本轮对应进程；不要输出 `.env` 内容。实际服务状态仍需现场检查。

### 自动生成测试文件（2026-10-04）

用户询问能否自动生成测试文件，现已为当前 smolagents 约定实现 `suite_generation.py`、`SuiteGenerationInput`、`generate-suite` CLI 和 `/api/assessment-suites/generate` API。网页新增案例数/种子输入、生成、JSON 预览/下载，生成的题集可直接开始评测。生成不访问目标或模型，标准答案由程序计算；每案例默认 30 秒与 2048 output Token，种子范围 0–2147483647，案例数 1–30，CLI 拒绝覆盖文件。

默认 12 题文件在 `examples/smolagents/suite.generated.json`。最大 30 题实际校准 `assessment_4074be29f4dd4e5da3ba8b330be9e82a` 为 30/30 pass、demo=true；报告在 `examples/smolagents/verified/generated.offline.md`。完整记录见 [SUITE_GENERATION_VALIDATION.zh-CN.md](SUITE_GENERATION_VALIDATION.zh-CN.md)。测试、网页、wheel、项目内 Skill 均已刷新。临时服务 18765/9092 已清理，原 `.agent-review` 未修改。

当前仅有 smolagents 模板；尚未实现根据任意仓库/接口用模型自动生成测试草稿。用户的可选范围澄清尚未回复，默认按当前目标实现。不要把此模板描述为通用仓库智能出题或任意正确答案推断。

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
阅读 examples/smolagents/README.md 和 examples/smolagents/VALIDATION.zh-CN.md。用户已配置 DeepSeek 密钥并授权真实测试：单题 1/1、完整题集 6/6 pass。服务本轮保持运行，继续前检查 9091/8765 状态，复用 review-data-smolagents 中的报告；不要打印凭据或无故重复付费测试。
自动生成题集已支持网页/CLI/API，见 SUITE_GENERATION_VALIDATION.zh-CN.md；默认12题文件已生成，30题上限校准已通过。当前模板只适用于 smolagents 示例约定，不支持根据任意仓库用模型自动出题。
下一阶段按比赛要求适配参赛 Agent、独立题集或构建来源。
复用现有 trace/Profile/external 协议；区分模拟候选、实际验证和未覆盖维度。
当前文档评估器只支持固定两页数字 PDF 和约定块 ID；不支持任意 PDF 自动对齐、OCR 或公式。
不把转换器输出当作 ground truth，不把简化指标称为官方 TEDS/CDM。
主动评测使用管理员目标表、固定题集和单进程服务，不认证源码到镜像来源或强制供应商费用。
测试使用独立 data-dir，避免修改原 .agent-review。wheel 与 skill 已重建，修改后须再次刷新。
```
