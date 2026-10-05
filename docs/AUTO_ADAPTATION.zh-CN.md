# 用 LLM 自动接入 Python Agent 仓库

## 网页操作

1. 启动 Docker，在项目根目录 `.env` 填好模型 URL、模型名和密钥。
2. 执行 `uv run agent-review serve`，打开 `http://127.0.0.1:8765/#/assessments`。
3. 填入公开 GitHub HTTPS 仓库链接，点击「拉取、部署并评测」。

自动识别顺序：仓库部署清单 → smolagents 固定配方 → Python LLM 自动适配。「配置构建与题集」可关闭未知仓库自动适配、调整修复次数（0–2），或显式选择 LLM 接入方式。仓库是独立应用、需要专用工具和领域数据时，上传自己准备的独立题集；不上传时，自动适配流程使用源码规划的合成探测。

任务列表显示生成、构建、接入检查、修复和正式评测阶段。可分别查看接入检查与正式结果、下载完整证据以及生成文件 ZIP。适配失败也保留已生成文件、失败原因和可见 Token；不会转成离线模型来获得成功结果。

## 模型配置

生成适配器优先使用带密钥的完整 `AGENT_REVIEW_LLM_API_URL/MODEL/TOKEN` 组；LLM 密钥留空时复用服务端已选择的被测模型配置，允许保留 `.env.example` 的预设地址和模型名。LLM 密钥已填但地址或模型缺失时需补齐，不混用其他组的密钥。被测 Agent 模型继续按 TARGET → 带密钥的 SMOL → LLM 的顺序选择。全部变量参考 `.env.example`，修改后重启服务。

```dotenv
AGENT_REVIEW_LLM_API_URL=https://api.deepseek.com
AGENT_REVIEW_LLM_MODEL=你的模型名
AGENT_REVIEW_LLM_TOKEN=你的密钥
AGENT_REVIEW_AUTO_ADAPT=true
AGENT_REVIEW_ADAPTATION_REPAIRS=1
AGENT_REVIEW_ADAPTATION_MAX_OUTPUT_TOKENS=
AGENT_REVIEW_ADAPTATION_THINKING=enabled
AGENT_REVIEW_ADAPTATION_TIMEOUT=900
AGENT_REVIEW_ADAPTATION_MAX_INPUT_CHARS=60000
```

地址使用纯文本。API 密钥留在本机 `.env`，只进入所需的 API 请求和目标运行环境，不交给生成模型充当源码材料，也不注入 Docker build。生成会将相关源码片段发送给上述供应商；网页提交即启动这项工作。

### 生成输出与思考模式

适配器生成默认不设置输出 Token 上限。`AGENT_REVIEW_ADAPTATION_MAX_OUTPUT_TOKENS` 未设置、留空或设为 `auto` 时，请求中完全省略 `max_tokens` 和 `max_completion_tokens`，由供应商默认设置和模型正常完成判断决定输出长度。DeepSeek 生成默认启用思考模式；本项目的 DeepSeek 示例配置显式开启：

```dotenv
AGENT_REVIEW_ADAPTATION_THINKING=enabled
AGENT_REVIEW_ADAPTATION_MAX_OUTPUT_TOKENS=
AGENT_REVIEW_ADAPTATION_TIMEOUT=900
```

修改后重启 `uv run agent-review serve`，再提交新任务。`THINKING` 仅控制适配器生成；留空时不发送该供应商扩展参数，`enabled`/`disabled` 仅适用于支持此参数的 API。其他供应商默认不发送该参数，更换 `.env.example` 的供应商时应按其接口设置或留空。网页显示实际生成上限（供应商默认或显式数值）和思考模式。

若希望主动设置生成上限，仍可填写正整数；生成配置已独立于评审配置，不再受共享评审器 32768 上限约束，实际接受范围由供应商决定。网页的「每案例输出 Token 上限」控制正式任务，与适配器生成分别配置。

不发送上限不会取消供应商自身的默认输出/上下文边界；当前 DeepSeek 文档说明思考模式未传 `max_tokens` 时默认 64K。生成请求默认等待 900 秒，允许配置为大于 0 且不超过 3600 秒，响应内存保护为 8 MiB，以容纳较长思考输出；仍可取消任务。原桥接代码和协议验证继续执行。

若返回 `finish_reason=length`，保存已知用量和生成设置后立即停止，不以同一供应商默认边界或同一显式上限自动修复。完整证据的每轮 `generation` 记录包含 `finish_reason`、`max_output_tokens`（供应商默认时为 null）、`output_limit_source`、`thinking`、`timeout_seconds` 和 `content_chars`；供应商返回 `completion_tokens_details.reasoning_tokens` 时单独统计思考 Token，该值已包含在输出 Token 中，不再次加入总数。旧失败记录不能补回未保存的结束原因或思考用量。

`AGENT_REVIEW_TARGETS` 用于登记已运行的 HTTP Agent 服务；网页提交仓库链接自动构建时可留空，不需要先启动 smolagents 示例。

供应商参数说明见 [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 实际生成哪些文件

|文件|生成或控制方|用途|
|---|---|---|
|`bridge.py`|LLM 生成，本地检查|调用原仓库的 Agent 入口，配置原生模型并转接请求/输出|
|`entry.json`|评审器校验后生成|原函数/方法路径、符号、定义行和文件哈希|
|`agent-review.json`|评审器生成|target-v1 路由、端口、运行期变量与资源限制|
|`Dockerfile`、`adapter-requirements.txt`|评审器按受限方案生成|安装原项目与声明的依赖，启动非 root 服务|
|`auto_runtime.py`|评审器固定模板|HTTP 协议、鉴权、会话、原入口观察、遥测与失败保存|

生成文件放在临时构建上下文，原固定 checkout 作为 `source/` 复制。不会向对方仓库提交文件，不修改原项目代码，也不会在宿主机导入或执行 LLM 生成的 Python。ZIP 不含原仓库源码，重用时需先把指定提交放到 `source/`，并检查 `provenance.json` 的验证状态。

LLM 接收有界的 README、依赖和 Python 文件片段，跳过 `.env`、敏感目录及符号链接；每文件最多约 6000 字符，最多 200 个候选文件，默认材料预算为生成输入限制的一半。大仓库中可能没有提供足够入口材料，不能由模型猜测通过。

## 接入检查与修复

先验证结构化方案、源文件/符号/定义行、桥接函数签名、依赖及环境变量。拒绝路径越界、任意生成 Dockerfile/宿主机命令和桥接代码中直接导入独立模型 HTTP 客户端。之后实际构建容器，健康检查，再运行一题独立的接入检查：

- 适配器通过 Python 调用观察器记录固定源文件中的指定函数是否执行。
- 响应必须通过 target-v1 校验，执行成功并附带匹配的入口证据。
- 接入检查成功后，重新启动正式评测容器，避免检查会话污染正式案例。
- 构建或检查失败，把有界、脱敏日志和异常类型反馈给 LLM；默认最多修复一次，总生成次数最多三次。
- 不支持、缺凭据、缺工具或取消时停止；供应商连接/超时错误不会自动重放不确定的付费请求。

入口观察属于容器自报证据，不是抗恶意篡改的认证。观察到入口执行不证明完整工作流未被绕过；生成代码和材料保留供复核。未观察到匹配入口的正式任务也不能取得能力通过结论。

## 可选模型网关

网页的“配置构建与题集”可勾选模型网关，CLI 使用 `--model-gateway`。网关在评审端保存实际请求和 Token，目标未返回或容器退出后仍保留已观测用量；目标自报记录保留为对照，不重复计数。首版仅支持本节的 Python 自动适配，监听与 Docker 访问配置见 [模型网关指南](MODEL_GATEWAY.zh-CN.md)。

## Token 和工具记录

三个用量分开保存：

1. **生成 Token**：适配模型 API 返回的 usage，包括已返回用量的失败/截断响应。
2. **接入检查 Token**：独立检查任务的实际记录，不计入正式题集。
3. **被测 Agent Token**：正式任务采集到的模型调用。

固定运行模板可观察通过 httpx 发往配置端点的同步/异步 JSON 或 SSE Chat Completions/Responses 请求；应用该任务的剩余期限和输出 Token 上限。原调用返回用量后记录输入、输出、总 Token、缓存/推理细分及请求 ID，不额外发送模型请求。桥接代码可用 `recorder.model/async_model` 包装其他原生模型调用，用 `recorder.tool/async_tool` 包装真实工具；显式模型包装与 HTTP 观察不重复计数。

SDK `usage`、`usage_metadata`、`token_usage` 中受支持的字段可采集。httpx 流式末尾用量和实际重试分别采集；其他网络库、未传播上下文的后台线程/子 Agent、未包装的工具可能没有记录。用量完整性和接入方式见 [通用采集指南](PROVIDER_TELEMETRY.zh-CN.md)。覆盖默认 `partial`，已采集 Token 显示 `≥数值`；没有数据保持未知，费用不凭 Token 估算。工具不存在于轨迹不能确认它没有被调用。接入失败同样保留已有模型/工具记录。

会话实例按 `session_id` 隔离，最多保留 64 个空闲/活动会话；原 Agent 自身的外部全局存储是否隔离，仍由正式题集检验。HTTP 期限不保证供应商调用立即停止计费。

## 题集与范围

标准答案来自评审端程序或上传的独立题集，不提供给适配生成模型或被测目标。自动源码规划测试结构化输出、指令、受控检索与干扰等通用约定，不认证仓库全部能力。领域 Agent 不支持这些输入/输出时，需要专项题集。

当前支持尝试接入普通 Python 函数、类和工作流入口。专用硬件、复杂外部数据库/浏览器服务、私有依赖、其他语言及超过片段范围的入口可能不能自动完成。构建成功、协议成功和任务能力通过是分别记录的结论。

CLI 同样可用（默认 CLI 后端仍是 offline，自动适配需真实模式）：

```sh
uv run --env-file .env agent-review assess-repo https://github.com/owner/python-agent \
  --backend openai --adaptation-repairs 1 --cases 3 \
  --data-dir ./review-data-auto --output ./auto-result.json
```

## 验证

自动化测试与实际 Docker 验收使用合成供应商，没有新增付费模型调用。实际管线验证了生成文件构建、原入口观察、target-v1、Token/工具采集和回收；尚未认证真实生成模型对陌生开源仓库的成功率。复现实测：

```sh
uv run python scripts/check_llm_adaptation.py
```

此脚本使用本地合成源码、合成模型 API 和实际 Docker；适用于支持 `host.docker.internal` 的 Docker Desktop。详细结果见 [验收记录](../AUTO_ADAPTATION_VALIDATION.zh-CN.md)。
