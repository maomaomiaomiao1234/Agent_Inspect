# Agent Trace Review

**把 Agent 的运行记录和输出导入本地，查看它做了什么、结果是否满足要求，以及用了多少资源。**

本项目适合复盘 OpenCode 会话，也支持把其他 Agent 的输入、输出和可见工具调用整理成通用 JSON 后评估。你可以在网页中检查证据、比较运行，并导出报告。

**第一次使用，建议按这个顺序：启动网页 → 加载通过示例 → 查看结果和证据 → 导入自己的数据。** 基础体验无需 OpenCode、Docker 或 API Token。

- [1. 安装并启动](#start)
- [2. 跑通第一个示例](#first-run)
- [3. 接入自己的数据](#own-data)
- [4. 体验代码修复和文档验收](#task-examples)
- [5. 导出、比较和保存数据](#reports)
- [6. 常见问题](#faq)
- [7. 进阶接口与开发](#advanced)

> 当前包版本为 0.3.0。本文以当前源码为准；目录中已有的 `dist/` 和 skill 内置 wheel 尚未同步最近的代码修复、文档评估功能，首次体验请采用下面的源码启动方式。

<a id="start"></a>
## 1. 安装并启动

### 准备环境

| 工具 | 用途 | 如何准备 |
| --- | --- | --- |
| uv | 管理 Python 环境和项目依赖 | 按 [uv 官方安装指南](https://docs.astral.sh/uv/getting-started/installation/) 安装 |
| Python 3.11+ | 运行后端 | 本文使用 3.12；uv 可在需要时下载，见 [Python 管理说明](https://docs.astral.sh/uv/guides/install-python/) |
| Node.js 和 npm | 从源码构建网页 | 使用 Node.js 20+；可从 [Node.js 官网](https://nodejs.org/en/download) 安装当前 LTS 版本 |

以下命令以 macOS/Linux 终端为例，本项目现有本机验收环境为 macOS。安装后重新打开终端，确认能运行：

```sh
uv --version
node --version
npm --version
```

### 安装项目依赖并构建网页

如果尚未取得项目文件，可通过已配置 SSH 权限的 GitHub 账号克隆：

```sh
git clone git@github.com:maomaomiaomiao1234/Agent_Inspect.git
```

进入包含 `pyproject.toml` 和 `web/` 的项目根目录。把第一行替换为你的实际路径；后文所有命令都在这个目录执行。

```sh
cd /path/to/Agent_Inspect
uv sync --python 3.12
npm --prefix web ci
uv run python scripts/build_web.py
```

首次安装需要联网下载依赖。看到 `Packaged web assets: ...` 表示网页已构建完成。

### 启动本地服务

```sh
uv run agent-review serve --data-dir ./.agent-review --port 8765
```

等终端显示服务启动成功后，在浏览器打开 **[http://127.0.0.1:8765](http://127.0.0.1:8765)**。

- 保持这个终端运行；按 `Ctrl+C` 停止服务。
- 后面需要执行 CLI 命令时，另开一个终端并进入同一项目根目录。
- 本文统一使用 `--data-dir ./.agent-review`。服务和导入命令必须指向同一数据目录，网页才能看到导入结果。
- 下次使用只需重新运行启动命令；更新前端源码后再执行构建命令。

<a id="first-run"></a>
## 2. 跑通第一个示例

### 在网页中体验

1. 在运行列表点击 **「通过示例 · 聚焦修复」**，加载后进入详情。
2. 确认验收结果为 **通过（pass）**。
3. 查看「执行轨迹」了解调用过程；查看「最终变更」了解最终 diff；在「验证记录」核对支持通过结论的报告。
4. 查看「指标与范围」，区分已观测、部分记录和未知的用量。
5. 点击「导出报告」，保存这次运行的评估。

再加载另外两个示例，观察证据变化如何影响结论：

| 网页示例 | CLI 场景名 | 预期结果 | 原因 |
| --- | --- | --- | --- |
| 通过示例 · 聚焦修复 | `focused` | `pass` | 有匹配最终状态和测试集合的通过报告 |
| 待补证据示例 · 反复定位 | `iterative` | `inconclusive` | 最后修改后缺少最终验收证据 |
| 失败示例 · 修复失败 | `failed` | `fail` | 有匹配最终状态和测试集合的失败报告 |

**这三个示例的日志和验证报告均为合成材料**，用于熟悉导入、判定和展示流程。加载它们不会运行真实 Agent 或调用模型；重复加载会复用已有运行。

### 也可以用命令行体验

```sh
uv run agent-review demo --scenario all --data-dir ./.agent-review
uv run agent-review list --data-dir ./.agent-review
```

命令会输出每条运行的 `run_id` 和结果。刷新网页即可查看；也可以直接导出：

```sh
uv run agent-review report RUN_ID --data-dir ./.agent-review --output report.md
```

**`RUN_ID` 是占位符**：请换成导入命令或 `list` 返回的完整 `run_...` 标识。`report.md` 保存在当前目录。省略 `demo` 的 `--scenario` 时只加载原有的两个示例，体验全部场景请保留 `all`。

### 如何理解结果

| 结果 | 含义 | 下一步 |
| --- | --- | --- |
| `pass` · 通过 | 已提供材料满足当前必需验收条件 | 检查标准是否覆盖你的真实需求 |
| `fail` · 未通过 | 至少一项必需条件有失败证据 | 打开对应检查，定位失败证据 |
| `inconclusive` · 待补证据 | 材料或验证不足，无法确认通过 | 补充参考、完整记录或独立验证结果 |

验收范围由规则和材料决定。命令退出码为 0、Agent 自报完成、没有诊断告警，都不能单独证明任务成功。缺失的 Token 或费用保持未知；单次运行也不能推出 Agent 的总体能力排名。

<a id="own-data"></a>
## 3. 接入自己的数据

先根据手上的材料选择入口：

| 你已有的材料 | 建议入口 | 还需要准备什么 |
| --- | --- | --- |
| OpenCode 导出的会话 JSON | [A. 导入 OpenCode](#opencode) | 若要严格验收代码结果，补充任务定义、diff 和独立验证报告 |
| 任意 Agent 的输入、输出、工具记录 | [B. 通用任务模板](#generic) | 转成通用 JSON，并定义 Profile 验收规则 |
| 只有任务输入和最终答案 | [C. 输入输出评审](#llm-review) | 评审标准、参考材料；调用模型时需要 API 配置 |
| 代码修复或 PDF 转换结果 | [内置专项示例](#task-examples) | 先理解固定示例，再为真实任务接入验证器或参考标注 |

几个会用到的名称：**Trace** 是运行记录和最终输出；**Profile** 是“怎样才算完成”的规则文件；**Run ID** 是导入后用于查询、评审和导出的标识；**Bundle** 是可重新导入的运行材料包。

<a id="opencode"></a>
### A. 我有 OpenCode 会话

**已有导出文件：** 点击网页「导入运行」，选择「轨迹 JSON 文件」，导入后打开详情。也可以执行：

```sh
uv run agent-review import session.json --data-dir ./.agent-review
```

**还没有导出文件：** 在安装了 OpenCode、且能访问目标会话的本机终端中执行，替换实际 session ID：

```sh
opencode session list
opencode export ses_YOUR_SESSION --pure > session.json
uv run agent-review import session.json --data-dir ./.agent-review
```

也可由本工具导出并导入指定会话：

```sh
uv run agent-review import-session ses_YOUR_SESSION --data-dir ./.agent-review
```

项目已用 OpenCode 1.18.18 验证原生导入链路；其他版本未全面验证。分析已有 JSON 不要求安装 OpenCode。`import-session` 只导出指定会话，不启动模型。

一个会话包含多个任务时，可用 `import` 的 `--first-message msg_START --last-message msg_END` 限定范围，起止消息均包含在内。未限定时会评估整个导出会话，耗时可能包含用户等待。

**要判断代码任务是否真正完成：** 在网页导入时同时提供 Task Manifest（任务定义）、最终 diff 和外部 JUnit 报告，或导入包含这些材料的 bundle。JUnit 上传支持单项测试检查；多项检查用结构化运行包。报告必须对应任务声明的最终代码状态和测试集合。导入器不会重新运行上传的测试，也不认证报告真实性。详见 [任务定义与验证材料](skills/opencode-trace-review/references/advanced.md#task-and-verification-materials)。

<a id="generic"></a>
### B. 我想评估自己的提取、调研或业务任务

从发票提取模板开始，先原样跑通，再修改为自己的任务：

```sh
uv run agent-review init-task ./my-task --template invoice
uv run agent-review import ./my-task/trace.json --profile ./my-task/profile.json --data-dir ./.agent-review
```

原样模板使用合成材料，预期结果为 `pass`。记录返回的 Run ID，刷新网页查看「最终结果」和「任务验收」。模板目录已存在且非空时，请换一个新目录。

接下来修改这些文件：

| 文件 | 你需要填写的内容 |
| --- | --- |
| `my-task/trace.json` | 实际任务 `task_prompt`、最终 `output`、可见 `events`、独立参考 `artifacts`；真实任务将 `demo` 改为 `false` |
| `my-task/profile.json` | 必需字段、预期值、资源阈值、工具使用约束等验收条件 |
| `my-task/external.profile.json`、`evaluator.py` | 可选：复杂业务需要自己的程序或人工核验时使用 |

修改后重新运行上面的 `import` 命令，并使用这次返回的 Run ID。网页也支持同时上传轨迹和「任务评估 Profile (.json)」。

- 只有输入输出时，`events` 可以为空，`coverage` 保留 `partial`；未采集的用量不要填写为 0。
- 同时更新模板中的标题、框架名、源运行 ID、模型名和时间；删除无法确认的可选示例值，避免把合成用量或时间带入真实报告。
- 替换模板的全部示例参考数据和阈值；参考答案应由验收方提供。
- `artifacts` 中需要放入实际可见材料；导入器不会自动打开其中的本地路径或 URL。
- `research` 模板可检查调研交付结构，但事实核验需要外部结果，因此直接导入通常为 `inconclusive`。

**只改验收规则时**，可以评估已经导入的运行，保存新的评估版本：

```sh
uv run agent-review evaluate RUN_ID --profile ./my-task/profile.json --data-dir ./.agent-review
```

字段存在、长度达标等规则只能证明对应条件满足。需要判断业务正确性时，接入独立参考、人工复核或专项评估器。完整格式与可运行的 external 示例见 [通用轨迹与自定义任务接口](skills/opencode-trace-review/references/custom-tasks.md)。

<a id="llm-review"></a>
### C. 我只有输入输出，希望让模型评审

先创建专用模板：

```sh
uv run agent-review init-task ./my-review --template llm-review
```

编辑 `my-review/trace.json` 的输入、答案和参考材料，修改 `my-review/profile.json` 的评审标准。真实任务将 `demo` 改为 `false`，然后导入：

```sh
uv run agent-review import ./my-review/trace.json --profile ./my-review/profile.json --data-dir ./.agent-review
```

在网页打开这次运行的 **「LLM 评审」**：

1. 填写兼容 Chat Completions 的 API 地址、模型名称和访问 Token。
2. 核对评审标准及将要发送的任务材料。
3. 点击 **「发送材料并评审」**，等待逐项结果。

导入本身不会调用模型；点击发送才会把任务输入、输出、内嵌材料、可见事件和规则发送到所填接口，可能产生费用。Token 不写入报告或浏览器存储，请勿写入项目文件。当前评审处理文本和 JSON；图片、文件或 URL 的内容需要先提取为可见材料。

模型评审处理 Profile 中的 `external` 条目，结论参与验收并标明模型来源；本地规则失败仍保留。没有可靠依据时可能返回未知。命令行配置、接口兼容选项和 HTTP/Python 用法见 [大模型评审指南](skills/opencode-trace-review/references/llm-review.md)。

原生 OpenCode 代码轨迹使用另一套可选 Scout Judge，配置方法见 [进阶 Judge 说明](skills/opencode-trace-review/references/advanced.md#optional-judge)；该 Judge 提供解释，不覆盖确定性验收结果。

<a id="task-examples"></a>
## 4. 体验代码修复和文档验收

这两组示例用于了解专项验收流程。候选由内置模拟器提供，尚未接入真实 Agent、SWE-bench 或 OmniDocBench。

### 代码修复：在 Docker 中实际运行固定测试

需要启动 Docker，并准备本地镜像；仅缺少镜像时运行下载命令：

```sh
docker pull python:3.12-slim
```

随后运行：

```sh
uv run agent-review code-repair --candidate all --data-dir ./.agent-review
```

| 候选 | 预期结果 | 含义 |
| --- | --- | --- |
| `correct` | `pass` | 正确修复，通过三项固定测试 |
| `incorrect` | `fail` | 边界缺陷仍然存在 |
| `regression` | `fail` | 修复边界时破坏了原本正常的行为 |
| `timeout` | `inconclusive` | 达到 10 秒执行限制，验证未完成 |

刷新网页，查看最终代码、diff、基线和最终测试报告。**候选是模拟的，Docker 测试是实际执行的。** Runner 只运行这些内置候选，使用受限、无网络的容器，不接受任意仓库或用户命令，也不自动拉取镜像。

如果只想看历史测试记录，可直接导入 [examples/code_repair](examples/code_repair/README.md) 中的 bundle，无需 Docker，也不会重跑测试。真实仓库需由你自己的 CI 或独立验证器生成报告后导入。实现和验收记录见 [Task 2 验证说明](TASK2_VALIDATION.zh-CN.md)。

### 文档转换：检查固定 PDF 的 Markdown / JSON 输出

无需 Docker 或额外 PDF 依赖。在网页点击「文档示例 · 正确」，或执行：

```sh
uv run agent-review document-conversion --candidate all --data-dir ./.agent-review
```

| 候选 | 预期结果 | 检查到的差异 |
| --- | --- | --- |
| `correct` | `pass` | 七项必需检查全通过 |
| `omitted` | `fail` | 正文内容遗漏 |
| `table_error` | `fail` | 表格单元格错误，可定位行列 |
| `order_error` | `fail` | 正文顺序错误 |
| `missing_reference` | `inconclusive` | 缺少独立参考，无法验收 |

详情页可阅读 Markdown、展开 JSON、下载源 PDF，并查看各项验收证据。**源 PDF 为自制文档，转换输出是模拟候选，固定评估器在本地实际执行。**

当前仅支持随项目提供的固定 PDF 和标注格式。你的其他 PDF 需要先准备独立参考标注、适配输出格式和专项评估器；目前没有“上传任意 PDF 自动转换并评分”的流程。OCR、公式、合并单元格和版式保真尚未覆盖。

已有材料见 [文档示例目录](examples/document_conversion/README.md)，验收记录见 [Task 3 验证说明](TASK3_VALIDATION.zh-CN.md)。如果导入的是文档 bundle，导入后需点击网页「运行固定文档检查」，或执行：

```sh
uv run agent-review document-evaluate RUN_ID --data-dir ./.agent-review
```

<a id="reports"></a>
## 5. 导出、比较和保存数据

### 导出报告或可移植材料

把 `RUN_ID` 替换为实际值：

```sh
# 给人阅读的评估报告
uv run agent-review report RUN_ID --data-dir ./.agent-review --output report.md

# 当前运行和选中的评估结果，便于程序处理
uv run agent-review report RUN_ID --data-dir ./.agent-review --format json --output report.json

# 可重新导入的源材料包
uv run agent-review report RUN_ID --data-dir ./.agent-review --format bundle --output run.bundle.json
```

`report` 默认选择最新评估，可用 `--revision REVISION_ID` 指定已保存版本。Bundle 不包含完整评估历史；自定义 Profile 或外部评估结果应另行保留，重导入后按相应流程重新验收。文档示例使用 `document-evaluate` 重跑固定检查。

### 比较两次运行

```sh
uv run agent-review compare RUN_ID_A RUN_ID_B --data-dir ./.agent-review
```

请使用同一任务、基线、环境、测试集合和预算下的运行。条件不完整或不同，工具只提供描述性对照。比较不会生成任意的 0–100 能力总分。

### 数据存在哪里

本教程的数据位于项目根目录的 `.agent-review/`，包含 `review.sqlite3` 和 `artifacts/`。停止服务及其他读写命令后，复制整个目录即可保留本地数据；只复制数据库会遗漏工件。

如需隔离不同实验，给所有相关命令传入同一个新的绝对 `--data-dir`。CLI 导入后刷新网页；目录不一致时，网页看不到那次运行。

基础分析保存在本机。显式执行 LLM 评审才会向配置的接口发送任务材料。导入会对常见密钥脱敏，但业务内容仍可能敏感；分享报告或 bundle 前请检查内容。服务仅监听 `127.0.0.1`，当前面向单机使用。

<a id="faq"></a>
## 6. 常见问题

| 问题 | 处理方法 |
| --- | --- |
| `uv`、`node` 或 `npm` 提示找不到命令 | 按第一节安装，并重新打开终端检查版本 |
| 网页无法连接 | 确认 `serve` 仍在运行、终端没有报错，使用它实际监听的端口 |
| 8765 端口被占用 | 改用 `uv run agent-review serve --data-dir ./.agent-review --port 18765`，访问 `http://127.0.0.1:18765` |
| API 能访问，但页面缺失或没有新示例按钮 | 在根目录重新执行 `uv run python scripts/build_web.py`，重启服务并刷新页面 |
| 导入成功，但网页找不到运行 | 确认两个终端在同一项目目录，且 `--data-dir` 一致，然后刷新页面 |
| `RUN_ID` 查询失败 | 使用 `list` 返回的实际 `run_...` 标识，检查数据目录是否一致 |
| 导入后显示待补证据 | 查看未完成的必需检查；补充 Profile、参考材料或独立验证，文档示例需显式重评 |
| 原始文本、任意框架日志或 PDF 上传失败 | 导入入口接受规定格式的 JSON；用模板转换运行材料，PDF 文件本身不能作为轨迹 JSON 导入 |
| `init-task` 拒绝创建目录 | 目标目录非空；换一个新目录，或继续编辑已有模板 |
| 代码示例提示 Docker 不可用或镜像缺失 | 启动 Docker，准备 `python:3.12-slim`；也可先导入历史 bundle 查看报告 |
| 想体验但没有 API Token | 使用第一组示例、本地 Profile 或文档示例即可；调用外部模型时才需要凭据 |
| wheel 或 skill 中找不到文档评估命令 | 当前已有分发包尚未更新，请按第一节从最新源码启动 |

<a id="advanced"></a>
## 7. 进阶接口与开发

日常使用读到这里已经足够。需要接入自己的系统时，可继续查看：

| 目标 | 文档或入口 |
| --- | --- |
| 通用 Trace、Profile、跨语言评估器、generic/2 代码证据包 | [自定义任务接口](skills/opencode-trace-review/references/custom-tasks.md) |
| API + Token 文本评审 | [LLM 评审接口](skills/opencode-trace-review/references/llm-review.md) |
| OpenCode Task Manifest、独立报告、Scout Judge | [进阶工作流](skills/opencode-trace-review/references/advanced.md) |
| HTTP API | 服务启动后打开 [交互式接口文档](http://127.0.0.1:8765/docs)；写请求要求 `X-Review-Request: 1` |
| 机器可读的数据合同 | `uv run agent-review schema --output schema.json` |
| 全部命令及参数 | `uv run agent-review --help`，或在子命令后加 `--help` |
| Skill 形式使用 | [opencode-trace-review](skills/opencode-trace-review/SKILL.md)；内置引擎需重新打包才能获得最新功能 |
| 产品设计与开发交接 | [实施计划](IMPLEMENTATION_PLAN.zh-CN.md)、[handoff](HANDOFF.zh-CN.md) |

### 开发与验证

```sh
uv sync --python 3.12 --extra dev
uv run pytest -q
uv run ruff check src scripts tests
uv run python scripts/build_web.py
```

可选依赖：`--extra scout` 用于原生代码轨迹 Judge，`--extra pdf-fixtures` 用于重建/核对固定 PDF。同步时同时列出需要保留的 extra。日常文档示例验收无需安装 `pdf-fixtures`。

前端开发使用 `npm --prefix web run dev`，后端保持在 8765 端口。浏览器测试需要 Chrome；先启动使用独立测试目录的服务，再在另一终端运行测试：

```sh
uv run agent-review serve --data-dir /tmp/agent-review-e2e --port 8765
```

```sh
npm --prefix web run test:e2e
```

### 生成新的分发包

```sh
uv run python scripts/build_web.py
uv build --wheel
uv run python scripts/check_package.py dist/agent_trace_review-0.3.0-py3-none-any.whl
uv run python scripts/package_skill.py
```

构建的 wheel 包含网页，安装后运行服务无需 Node.js。Skill 内置引擎需要最后一步刷新；本文开头的旧包提示描述的是当前目录中已有的分发文件。

项目核心代码位于 `src/agent_trace_review/`，网页位于 `web/src/`，验证脚本位于 `scripts/`。目前未实现实时采集、任意 Agent 框架自动适配、任意仓库验证调度和团队云端部署。真实任务接入请从可见材料、明确标准和独立验证开始。
