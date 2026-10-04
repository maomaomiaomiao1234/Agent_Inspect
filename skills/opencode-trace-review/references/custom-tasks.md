# 通用 Agent 与自定义任务评估

需要接入大模型评审 input/output 或开放式答案时，见 [API + Token 评审](llm-review.md)。可直接使用 `init-task --template llm-review` 和 `llm-review` 命令。

适用于提取、调研、文档处理、浏览器和业务流程等任务的可见轨迹。先把记录转换为通用 JSON，再提供任务规则或专项评估器。当前不会自动识别任意框架日志。

下文 `agent-review` 表示引擎命令。通过 skill 使用时替换为 `python3 "$REVIEW_SKILL/scripts/run_review.py"`。操作使用同一个绝对 `--data-dir`；RUN_ID 使用导入返回值。

## 从模板开始

```sh
agent-review init-task /absolute/my-task --template invoice
agent-review import /absolute/my-task/trace.json --profile /absolute/my-task/profile.json --data-dir /absolute/review-data
agent-review report RUN_ID --data-dir /absolute/review-data --output /absolute/report.md
```

invoice 包含合成轨迹、JSON 规则、external 规则及可运行的 Python 评估器。research 检查交付结构，并将事实核验保留为必需的 external 检查；没有专项结果时保持 inconclusive。模板拒绝覆盖非空目录。

通常只需修改 profile.json；复杂业务再修改 evaluator.py，或用其他语言实现同一协议。示例标准答案必须替换为用户真实的验收标准。

## 通用轨迹合同

```json
{
  "trace_version": "1",
  "framework": "my-agent",
  "run_id": "task-001",
  "coverage": "partial",
  "events": [
    {"id": "call-1", "kind": "tool", "tool": "ocr.extract", "effect": "read", "status": "completed", "input": {"document": "invoice"}, "output": "CNY 128.50"},
    {"id": "model-1", "kind": "llm", "status": "completed", "usage": {"tokens": {"input": 120, "output": 30, "total": 150}, "cost_usd": 0.001}}
  ],
  "output": {"amount": 128.5, "currency": "CNY"},
  "artifacts": {"reference_invoice": {"amount": "128.50"}}
}
```

- events 按执行顺序排列，id 唯一。kind 支持 tool/llm/message/retry/error；不接收隐藏推理文本。
- effect 为 read/write/unknown，仅明确只读的调用参与重复读取检测，不能根据陌生工具名猜测。
- status 为 completed/error/running/skipped/unknown；未知结果不能填写 completed。
- usage 只放在独立 llm 调用事件上，不能同时记录父任务合计和子调用。未知字段省略，不填写 0。cost_usd 已换算为美元；引擎不换汇或查询价格。
- 事件和轨迹可提供 start_ms/end_ms，单位毫秒；数组顺序决定行为先后。
- coverage 默认为 partial。complete 是导出方对全部调用的声明；部分轨迹不能证明工具未被调用，资源阈值也可能保持 unknown。
- output 是最终 JSON 结果，artifacts 是内嵌辅助材料。导入器不读取其中引用的路径或 URL。参考答案应由验收方提供。
- 可选 task_prompt/title/agent_version/models/demo。合成数据设置 demo: true。
- 调用事件还可提供 model（仅 llm）、单调时钟测量的 duration_ms、parent_id、provenance 与 context。主动 HTTP 评测的可选 target-trace-v1 会转换到这些事件；逐次用量与轮次汇总不重复累计。完整覆盖是采集方声明，缺少内部记录的旧目标仍保持 partial。

其他框架需编写转换器，明确映射上述字段，不补造未观测数据。完整机器合同见 `schema --output schema.json` 中的 generic_trace。

## JSON 任务规则（Profile）

```json
{
  "profile_version": "1", "id": "invoice-extraction", "version": "1",
  "rules": [
    {"id": "amount", "dimension": "outcome", "op": "equals", "path": "/output/amount", "value": 128.5},
    {"id": "budget", "dimension": "resource", "op": "max", "path": "/metrics/tokens_total", "value": 500},
    {"id": "no_email", "dimension": "behavior", "op": "tool_forbidden", "value": "email.send"}
  ]
}
```

id 唯一，dimension 默认 outcome，required 默认 true。内容变化会改变 Profile 哈希，即使没有修改 version，也不会与旧标准视作相同。

| op | 用途 |
| --- | --- |
| exists | 字段存在；若整个材料缺失则 unknown |
| equals | JSON 值相等，布尔值与数字严格区分 |
| contains | 字符串包含、数组包含一个值、对象包含键 |
| min / max | 数值阈值 |
| length_min / length_max | 字符串、数组、对象的长度/数量 |
| tool_required / tool_forbidden | 是否观察到指定工具调用，不推断执行效果 |
| external | 用户自己的程序或人工验收 |

path 使用 JSON Pointer，如 /output/amount、/artifacts/reference_invoice/amount、/events/0/output、/metrics/cost_usd。键名中的 / 写为 ~1，~ 写为 ~0。事件 output 是显示文本，复杂结果放在顶层 output 或 artifacts。Profile 不支持动态代码和命令。

每项结果为 pass/fail/unknown。缺字段一般为 unknown；必填字段增加 exists 规则。部分指标不能证明满足预算，脱敏值不能证明匹配。整体通过需要全部必需检查通过，且存在任务结果验收；可选检查不单独阻止通过。代码任务原有验收失败不会被 Profile 覆盖。结构符合不证明内容真实。

## 自定义评估器：跨语言 JSON 接口

声明 `{"id":"invoice_total","op":"external"}` 后：

```sh
agent-review evaluator-request RUN_ID --profile /absolute/my-task/external.profile.json --data-dir /absolute/review-data --output /absolute/my-task/request.json
python3 /absolute/my-task/evaluator.py /absolute/my-task/request.json /absolute/my-task/results.json
agent-review evaluate RUN_ID --profile /absolute/my-task/external.profile.json --results /absolute/my-task/results.json --data-dir /absolute/review-data
```

第二步由用户选择执行；引擎不会自动加载或执行评估代码。可改用 Node、Go、人工复核或外部服务；自行控制数据发送与调用费用。

请求包含 protocol/run_id/input_hash/profile_hash/context/checks。context 包含可见输入快照、output、artifacts、events、metrics 和 metric_status；checks 只列 external 规则，可通过规则的 value 传入自定义参数。

响应保留请求的四个身份字段，增加 results：

```json
{
  "protocol": "agent-review/evaluator-v1",
  "run_id": "使用请求里的值",
  "input_hash": "使用请求里的值",
  "profile_hash": "使用请求里的值",
  "results": [
    {"id": "invoice_total", "status": "pass", "explanation": "与独立参考金额匹配", "evidence_paths": ["/output/amount", "/artifacts/reference_invoice/amount"]}
  ]
}
```

这仅展示结构。status 为 pass/fail/unknown；pass/fail 必须引用 context 中存在的路径。输入/规则变化、未知规则 ID、重复结果或不存在的引用会被拒绝。缺少的 external 结果保持 unknown。报告保存规则、结果和证据快照；哈希绑定不能认证作者或证明评估器正确运行。

evaluate 返回 revision_id；`report RUN_ID --revision REVISION_ID` 导出确切版本。每个版本使用本次指定的完整 Profile，不自动合并历史规则；需要同时保留字段、预算和外部验收时，把它们放在同一 Profile 中。JSON 报告包含 Profile 和检查结果。可移植 bundle 仅保存源轨迹与任务材料，不含评估历史；复现时保留 Profile、请求和结果，并重新评估。

## HTTP 与 Python 接口

本地服务：`agent-review serve`。写请求包含 `X-Review-Request: 1`；JSON 请求还需 `Content-Type: application/json`。

- POST /api/imports：multipart 的 file，可附加 profile_file。
- POST /api/runs/{id}/evaluator-request：请求体为 Profile JSON。
- POST /api/runs/{id}/profile-evaluations：请求体为 `{"profile": Profile, "results": 可选响应}`。
- GET /api/schema：完整合同；/docs 提供交互式 API 文档。

安装引擎包后也可调用 Python 接口：

```python
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.profiles import evaluator_request, evaluate_profile

store = Store("review-data")
run, evaluation, created = ingest(store, trace_bytes, profile=profile_dict)
request = evaluator_request(run, profile_dict)
# 自己的受信任程序执行专项评估，生成 response_dict。
evaluation = evaluate_profile(store, run, profile_dict, response_dict)
```

正式比较需提供相同任务、初始输入状态、环境、验收集合和预算。可用 Task Manifest 的 initial_state_hash 表达非 Git 初始状态，checks 留空时由 Profile 验收。缺少条件仅作描述性比较。

## generic/2：代码 diff 与独立验证报告

最新项目源码增加 `generic/2`，机器合同位于 `schema` 的 `generic_bundle`。旧 `generic/1` 的格式与运行 ID 规则保持不变；它仍不接受 diff 或验证报告。

```json
{
  "bundle_version": "generic/2",
  "trace": {"trace_version": "1", "framework": "my-agent", "run_id": "repair-001", "events": [], "output": "修复产物"},
  "task": {
    "id": "repair", "initial_state_hash": "initial-content-hash",
    "final_state_hash": "final-content-hash", "suite_hash": "fixed-verifier-hash",
    "checks": [{"id": "tests", "kind": "test"}]
  },
  "diff": "",
  "verifications": [{
    "id": "final-tests", "check_id": "tests", "kind": "test", "phase": "final",
    "provenance": "external_verifier", "result": "pass",
    "state_hash": "final-content-hash", "suite_hash": "fixed-verifier-hash",
    "cases": {"Example::test_boundary": "pass"}
  }]
}
```

上例只说明结构，hash 和报告应由执行方提供。`diff: ""` 表示已提供空补丁；`null` 或省略表示未提供。完整 cases 与计数必须一致，报告 ID 不可重复，只接受 external_verifier。导入器会重新建立本地证据 ID，不接受上传者指定的本地引用。导出 bundle 保留 diff、报告和任务材料；材料变化会改变 run ID。

HTTP 附加 JUnit 必须明确上传 generic/2 envelope，且 Task 只有一个 test check，并提供 final_state_hash 与 suite_hash。裸通用 trace 和 generic/1 不会隐式升级。导入文件仍不执行命令，也不认证报告真实性。

### 内置代码修复实验

在**项目源码目录**运行：

```sh
uv run agent-review code-repair --candidate all --data-dir /absolute/review-data
uv run agent-review serve --data-dir /absolute/review-data
```

需运行中的 Docker 和本地 `python:3.12-slim`。引擎不自动拉取镜像；缺少时手动执行 `docker pull python:3.12-slim`。correct=pass、incorrect=fail、regression=fail（含测试回归证据）、timeout=inconclusive。每次执行形成新运行，重新导入同一材料保持幂等。

仅支持内置候选模拟，不接收任意命令或仓库。候选不是实际 Agent 输出；baseline/final 报告来自实际 Docker 测试，原始日志、退出码、耗时、代码快照、verifier 及镜像 ID 保存在 artifacts。未调用模型，Token/费用保持未知。测试集合由评测方固定；容器无网络、无宿主机挂载、非 root、只读根目录并限制 CPU/内存/PID。每阶段单独创建容器，异常与取消时清理；清理失败会给出容器名称并报错。

**分发边界：仓库 `dist/` 和 skill 内置 wheel 尚未重新打包，不包含此功能。** 修改源码后使用 `uv run`；要分发新版，需另行构建、验证并刷新 wheel。当前材料哈希提供可复现关联，不是数字签名或可信执行证明。

## 固定文档转换样例（Task 3）

项目最新源码支持 `document-conversion --candidate correct|omitted|table_error|order_error|missing_reference|all`。使用同一 `--data-dir` 启动网页即可查看 Markdown、JSON、源 PDF 和逐项证据。候选是模拟，固定独立参考检查实际执行；未调用转换 Agent、OCR 或模型。

源文档是自制两页英文数字 PDF；标注在 `fixtures/document_conversion/reference.json`。评估器从安装包读取可信参考，核对内嵌 PDF 字节哈希和导入参考快照；缺失或修改参考保持未知，不将候选提供的答案作为 ground truth。

输出合同见 `schema.document_output`：`output.document` 包含 document_id、page_count、blocks；块以 id、page、kind 及 text/level/rows 表示，`output.markdown` 提供独立 Markdown。当前需要预先对齐的固定块 ID；表格为矩形，Markdown 为标题、段落和简单竖线表格。不是通用 PDF 自动对齐/转换接口。

七项必需 external 检查分别核对文档身份、完整性、正文、标题、顺序、表格及 Markdown/JSON 一致性。大小写、标点与单位均保留，只规范化 Unicode NFC 和空白。OCR 与公式返回 unknown；默认非必需，改成必需会阻止通过。合并单元格、公式、OCR 和版式保真未实现，不宣称官方 TEDS/CDM。

导入 bundle 不自动执行评估器。显式 `document-evaluate RUN_ID --data-dir ...` 重算固定文档检查；或 `evaluator-request` → `document-evaluator request.json --output results.json` → `evaluate --results results.json` 复用现有 external 协议。响应绑定当前输入/规则哈希，绑定不能证明第三方上传结果的真实性。标准 bundle 保存源材料，不自动携带/信任 Profile 评估历史。

已验证运行包、请求和响应在 `examples/document_conversion`。普通评估不需要额外 PDF 依赖；重建与渲染 fixture 才需要 `pdf-fixtures` extra。skill 内置 wheel 仍为旧版，这些命令目前应从项目源码通过 `uv run` 使用。
