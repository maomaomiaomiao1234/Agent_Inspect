# 新增功能审核与仓库自动评测能力核查

审核日期：2026-10-04。范围：主动评测、仓库扫描、受管理 Docker 目标、smolagents 适配器、自动生成题集及对应 API/CLI/网页。当前目录没有 `.git`，范围依据交接记录与现有源码确定，不能确认每个问题首次引入的提交。

结论：发现 3 项需要修复的问题。当前支持预先接入目标后的自动评测，以及启动本机已有的固定 Docker 镜像；尚不支持仅提供仓库 URL 即自动拉取、构建、部署、适配和评测。自动出题只覆盖当前 smolagents 示例约定。

本次未修改业务代码，没有读取真实密钥、调用付费模型或启动真实 Docker 目标。

## 1. [P1] 评审进程异常退出后，受管理容器无法自动回收

位置：[target_client.py](../../src/agent_trace_review/target_client.py) 第 136、172、190–215 行；[assessment_store.py](../../src/agent_trace_review/assessment_store.py) 第 134–141 行；[assessments.py](../../src/agent_trace_review/assessments.py) 第 405–410 行。

触发：评测已通过 `docker run -d` 启动目标，评审进程被强制终止或崩溃，Docker daemon 继续运行。

容器名只保存在 `deployed_target()` 的局部变量中，部署记录未保存容器名/ID，也没有关联 job 的 Docker label。清理仅在 Python `finally` 中执行。异常退出会跳过这一段，重启恢复只把数据库中的 queued/running 改为 interrupted，没有检查或回收对应容器。因此任务显示中断后，目标仍可能占用资源并继续执行外部调用；重复重启会留下多个容器。

复现：在独立子进程中用 Docker 调用替身模拟启动，写入真实 SQLite 任务，再用 `os._exit(23)` 模拟异常退出。重建 `AssessmentManager` 后，任务为 interrupted，但容器存活标记仍在，持久化部署字段不包含容器身份。这是异常退出控制流和恢复逻辑的模拟验证，未运行真实 Docker 崩溃试验。

建议：创建前持久化 job 与容器名的对应关系，给容器添加该评审实例/job 的标签；启动恢复时枚举并回收本实例遗留资源，记录清理失败并允许重试。正常结束、取消、异常退出后恢复都需要覆盖，不能只依赖 `finally`。

## 2. [P2] smolagents 默认完整历史模式在第二轮返回 422

位置：[agent_server.py](../../examples/smolagents/agent_server.py) 第 43–46 行；[assessments.py](../../src/agent_trace_review/assessments.py) 第 179–180、230 行。

评测端把 `response.output` 原样作为 assistant 消息的 `content`；协议允许 output 为 JSON 对象，smolagents 实际也返回对象。但适配器的 `Message.content` 只允许字符串。使用默认 `history="full"` 时，第二轮包含上一轮对象，适配器在运行 Agent 前就返回 HTTP 422，结果变为 inconclusive。

复现：使用实际 smolagents 1.26.0 offline 框架、ASGI 请求和评测编排器执行两轮记忆案例。full 模式得到 `execution_state=error / error=http_422`；相同题目使用 current 模式通过。未访问真实模型。

现有例题与适配测试都使用仅当前消息的第二轮，所以 8 项适配测试及生成题集校准未覆盖此问题。生成器当前输出的记忆题不受此问题影响；用户按默认值编写多轮题目时会受影响。

建议：统一目标请求与适配器的消息契约，允许有界 JSON content，或在评测端明确序列化并一致应用该约定；加入真实编排器到适配器的 full/current 两组端到端测试。

## 3. [P2] 部分用量缺失会使已知 Token 消耗不再扣减

位置：[assessments.py](../../src/agent_trace_review/assessments.py) 第 182–192 行及第 138–156 行。

预算只在 `len(reported_outputs) == len(turns)` 时扣减。任一轮没有 usage 或 output Token，后续所有轮次都会继续获得完整预算，即使已收到的其他轮次用量已达到/超过总预算。汇总同样以完整用量为前提，已知超额也被显示为 unknown。

复现：案例总 output 预算为 5；第一轮缺少 usage，第二轮自报 output=6。评测器仍发起第三轮，三次请求的预算均为 5。第三轮再自报 output=6 后，已知消耗下限为 12，汇总仍为 `output_token_budget=unknown`，执行完成且答案规则为 pass。

问题在于未执行已有预算停止规则、丢失明确超额信息；这不等同于要求评测器能强制停止供应商计费。答案正确与资源违规仍可分别展示。

建议：始终扣减已知用量，以其作为消耗下限；下限已耗尽时停止后续请求。缺失用量时总量保持 unknown，但已知下限超过预算时应明确报告 fail。

## 协议一致性补充

[assessment_contracts.py](../../src/agent_trace_review/assessment_contracts.py) 第 137 行给响应 protocol 设置了默认值，所以仅返回 `{"output": ...}` 也会被接受，补入 target-v1 并可能得到 pass。这与主动评测指南中“缺少有效协议”会被拒绝的描述不一致；现有测试也依赖省略 protocol 的响应。建议明确选择兼容省略还是强制版本声明，并统一文档与测试。本项属于契约一致性问题，没有把它作为上述三个主要缺陷之一。

## “知道仓库后自动拉取、部署并评测”的现状

| 环节 | 当前支持程度 | 代码依据 |
| --- | --- | --- |
| 根据 URL 拉取仓库 | 未实现 | `inspect_repository()` 要求本地目录；`repository_url` 只用于校验和生成源码链接，没有 clone/fetch 流程 |
| 固定版本并扫描 | 已有 | 解析本地 Git ref 为 commit，读取该提交的有限文本快照 |
| 安装依赖、构建目标镜像 | 未实现 | `deployed_target()` 只 inspect 本地镜像，并使用 `--pull=never`；没有目标 build/pull |
| 启动、健康检查、评测结束清理 | 部分已有 | 可启动管理员登记的本地固定 sha256 镜像；异常退出回收有上述缺陷 |
| 自动识别目标接口 | 未实现 | 必须实现 `agent-review/target-v1`；endpoint/路由/环境变量由管理员登记 |
| 按任意仓库生成有效测试 | 未实现 | 只有 smolagents 固定工具与 JSON 输出模板；不会自动把扫描到的能力声明关联到题目 |
| 自动执行任务并产出报告 | 已有 | 冻结 Suite、独立 Profile、多轮/隔离、预算统计、比较与报告导出 |
| 证明运行的是给定仓库提交 | 未实现 | 当前部署明确记录 `source_binding=unverified`，源码与镜像分别登记 |

现有 smolagents 示例也是手工安装、手工 clone、手工启动 HTTP 适配器，再由评审器发题。部署评审服务本身已有 Dockerfile/Compose；Compose 使用已部署 HTTP 目标，未包含通用源码构建器。

## 可落地的扩展方案

建议先支持“已适配仓库的一键评测”，以仓库 URL + ref + 部署配置文件为输入，再扩展已知框架模板。仅凭 URL 无法可靠得知私有依赖、必需凭据、服务入口、任务接口和独立正确答案；未知仓库应输出缺失项与失败阶段。

可以在现有评测器之前增加以下流程：

1. **获取源码**：clone/fetch 到独立工作目录，固定 commit，限制大小与耗时，保存获取记录。
2. **读取部署配置**：声明 Dockerfile、上下文、端口、健康检查、适配器、必要环境变量名称和题集；对已支持框架使用固定模板。
3. **隔离构建**：在专门构建环境中安装依赖和生成镜像，记录构建日志、锁文件、commit、适配器版本及镜像 digest。仓库安装脚本属于代码执行，应与评审进程、标准答案和评审凭据隔离。
4. **部署及协议预检**：启动受限制的容器，注入明确配置的目标凭据，检查健康与请求/响应契约。
5. **独立评测**：按支持的能力选择冻结题集，运行现有 `prepare_assessment()` / `run_assessment()`；预期答案不能直接采用目标自己的输出或 README 声明。
6. **归档及恢复**：以持久化状态串起获取、构建、启动、评测、清理；每个阶段可定位失败，重启可回收资源并避免无意重放付费任务。

配置清单和题集准备完成后，日常操作可以做到提交仓库与版本后一键运行。支持范围应按已验证的适配器/模板声明，不能宣传任意仓库零配置部署与通用能力评分。

## 本次验证

- 根项目 pytest：279 passed，2 个已有依赖弃用警告。最初有 8 项因沙箱禁止端口监听而报错，获准在允许监听的环境重跑后全部通过。
- smolagents 独立测试：8 passed，模型 SDK 测试只使用本机模拟服务。
- Ruff：`src scripts tests` 及 smolagents 适配器/测试通过。
- TypeScript：`tsc -b web/tsconfig.json --pretty false` 通过。
- 额外复现：完整历史/当前历史对照、部分 usage 预算、缺失协议、异常退出恢复模拟，结果见同目录 `2026-10-04-reproductions.json`。
- 没有重新运行浏览器 E2E、真实模型或真实 Docker 部署；不能把以上检查解释为这些场景均已验证。

复现脚本：同目录 `reproduce_20261004.py`，从项目根目录运行 `examples/smolagents/.venv/bin/python output/reviews/reproduce_20261004.py`。脚本使用临时数据库、offline 框架、内存 HTTP transport 与 Docker 替身，运行结束回收临时文件。
