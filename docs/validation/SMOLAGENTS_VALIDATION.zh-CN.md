# smolagents 接入验证

验证日期：2026-10-04。首次使用真实 smolagents 框架、固定工具与 HTTP 服务，模型决策由脚本生成，属于接入校准；后续真实 DeepSeek 模型评测单独记录在下文。

## 固定环境

- 上游：[Hugging Face smolagents](https://github.com/huggingface/smolagents)，Apache-2.0。
- 版本：`1.26.0`；源码提交：`12c1bc820eca50ace6f80a21d90426d41d74f845`。
- 示例依赖在独立 `examples/smolagents/.venv` 中安装，由本目录 `uv.lock` 固定；不更改评审服务依赖。
- 已核对安装包的 `agents.py`、`models.py`、`tools.py` 与固定源码提交的对应文件字节一致。实际使用 `ToolCallingAgent`，不执行模型生成的 Python。

## 首次 offline 校准完成的检查

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 适配层 pytest | 7 passed | 真实框架工具调用、两步计算、会话记忆/隔离、轮次拒绝、请求限制、访问令牌、配置检查、SDK 工具闭环、逐轮用量和密钥脱敏 |
| Ruff | 通过 | `agent_server.py` 与 `tests/test_adapter.py` |
| 评审器主动调用 | 6/6 pass | localhost 上真实 HTTP 健康检查和 7 次任务请求；使用独立固定 Profile 验收 |
| 当时的清理 | 完成 | offline 临时 Agent 服务已停止，9091 监听已释放；后续真实服务状态见下文 |

SDK 测试使用临时本地 Chat Completions 协议模拟器；它验证 SDK 参数、工具调用、用量统计与脱敏路径，不验证任何真实提供商或模型。未使用真实密钥，未产生付费模型调用。

主动评测运行 ID：`assessment_2dd4b8cc8282458a82831e0433280ab2`；目标为 `smolagents-offline`，`demo=true`。算术、两步计算、两轮记忆、新会话隔离、公开输入干扰六项通过。第二轮记忆请求只发送当前问题，由目标保留框架记忆与工具状态。

HTTP 轨迹覆盖为 partial。输出中的 `_execution` 工具记录标记 `target_reported`，没有认证为独立内部轨迹。模型 Token 和费用未提供，保持 unknown；源码关联属于指定线索，不证明部署来源或因果关系。

## 归档与复现

- [可读报告](../reference-assets/examples/smolagents/verified/offline.md)
- [主动评测结果](../../examples/smolagents/verified/offline.assessment.json)
- [题集、源码档案与逐案例证据包](../../examples/smolagents/verified/offline.bundle.json)

题集 SHA-256：`ce86774779d956468adcfa68af95579ef9e06b49053cfc3da210ddaa3184d1b9`。

| 文件 | SHA-256 |
| --- | --- |
| 当时的 agent_server.py | `a7f1df2973b6129e732dfc271375d0f63a9e04b23c4af82f0ca1324dd5f241b3` |
| uv.lock | `0fb9e69afd4cb3a3ee9ace8954ad443c22f89f2f48a1c5ecdba88e62637ca206` |
| verified/offline.md | `e90e146326403de90439507f4b8b1e01dca098cd54381e6792756e7801b05700` |
| verified/offline.assessment.json | `ec25e4b424d41fd7cf6573634b6600edc6fe14a2701486854330b4410b04bd68` |
| verified/offline.bundle.json | `b3219d8571e74a69cd2b7deeec62c973f3a4d98eca4e70cdce62952b36fc8509` |

运行命令、API 配置和网页操作见 [README](../examples/smolagents/README.md)。本轮评审数据保存在独立目录 `tmp/smolagents-calibration-20261004`；未修改原 `.agent-review`。

## DeepSeek 配置更新（2026-10-04）

用户选用 DeepSeek。根据 [官方接入说明](https://api-docs.deepseek.com/zh-cn/)，配置示例现固定地址 `https://api.deepseek.com` 和模型名 `deepseek-flash`；密钥留空，在本机填写。增加 `suite.smoke.json`，先验证一个算术案例再运行完整题集。

识别官方域名时发送 `thinking.type=disabled`，兼容 smolagents 使用的 `tool_choice=required`；依据 [DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)，后者在思考模式下会被拒绝。其他提供商不收到该专用参数。

更新后适配层测试 **8 passed**、Ruff 通过。新增测试通过真实 SDK 和 `httpx.MockTransport` 核对最终请求 URL、模型名、思考开关、工具选择和两步工具闭环；没有访问真实 DeepSeek。上表源码哈希对应首次 offline 校准时的版本；归档结果未覆盖本次配置更新，未更改历史证据。

## 真实 DeepSeek 首次评测（2026-10-04）

用户在本机配置密钥并授权启动测试后，实际调用 `https://api.deepseek.com` 的 `deepseek-flash`，使用非思考模式。目标为 `smolagents-model`，`demo=false`。标准答案仍由独立固定 Profile 保存在评审端。

| 运行 | 实际任务 ID | 结果 | API 自报总 Token |
| --- | --- | --- | ---: |
| 单个算术案例 | `assessment_3a5cbb9a7bb74143ab4e555553763c22` | 1/1 pass | 3261 |
| 完整固定题集 | `assessment_5b27fb7af4ba4ba9a69d373d7e714515` | 6/6 pass | 24898 |

完整题集在北京时间 2026-10-04 13:01:47 至 13:01:59 执行。加法、乘法、两步工具调用、两轮记忆、新会话隔离、固定公开输入干扰案例均通过，平均观测案例耗时 1964.469 ms。该数值来自单次本机运行；没有多次重复实验或统计置信区间。单题与完整题集分别观测到 1 和 7 个 HTTP 轮次，目标分别报告 2 和 15 次模型调用。

Token 包含输入及输出，用量来源为目标转报 API 数据；本次 output Token 预算检查均通过。提供商没有返回费用，账单金额保持 unknown。HTTP 轨迹覆盖继续为 partial，内部工具记录标记目标自报；本轮固定干扰题通过不代表全面的提示注入防御能力。

报告、结果和证据包已保存到本机 `review-data-smolagents/reports/`，该目录已加入忽略规则。完整题集三份工件的 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| deepseek.md | `bf5c1a5f18e9f63076d8cee0a5884e1e2418a52039965f4a28fcc50953f8daa2` |
| deepseek.assessment.json | `535d97edf2e0e5f7e723f95cc8083789cfc121e8f5a8d80fb1eaee7b7afebac6` |
| deepseek.bundle.json | `9dabfc4fba3620a47c631cd6501591e32ce4504cec24b6c71bcb6289b6d49848` |

运行时适配器 SHA-256：`da1995e3d08af267c27c91eb1797fd9491e662d07400dd2ba6df3064c7bf405d`；题集和执行器哈希与上述完整 offline 校准相同。已检查六份导出工件均未包含配置的 API 密钥；没有把密钥写入文档或公开归档。

本次通过评审服务 API 提交队列任务，网页与 CLI 没有并发启动两个评审执行器共用目录。服务保持运行：被测 Agent 为 `127.0.0.1:9091`，评审网页为 `127.0.0.1:8765/#/assessments`，健康检查及网页请求均返回 200。服务停止后，评审结果仍保留在自己的 data-dir。

## 后续验证范围

已验证当前 DeepSeek 配置在这六项固定案例上的兼容性及结果。更复杂题集、其他模型/提供商、重复实验、完整提示注入测试、源码到部署的可信绑定和比赛基准成绩尚未验证。
