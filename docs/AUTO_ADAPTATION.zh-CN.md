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
AGENT_REVIEW_ADAPTATION_MAX_OUTPUT_TOKENS=8192
AGENT_REVIEW_ADAPTATION_TIMEOUT=90
AGENT_REVIEW_ADAPTATION_MAX_INPUT_CHARS=60000
```

地址使用纯文本。API 密钥留在本机 `.env`，只进入所需的 API 请求和目标运行环境，不交给生成模型充当源码材料，也不注入 Docker build。生成会将相关源码片段发送给上述供应商；网页提交即启动这项工作。

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

## Token 和工具记录

三个用量分开保存：

1. **生成 Token**：适配模型 API 返回的 usage，包括已返回用量的失败/截断响应。
2. **接入检查 Token**：独立检查任务的实际记录，不计入正式题集。
3. **被测 Agent Token**：正式任务采集到的模型调用。

固定运行模板可观察通过 httpx 发往配置端点的非流式 Chat Completions/Responses 请求；应用该任务的剩余期限和输出 Token 上限。原调用返回用量后记录输入、输出、总 Token，不额外发送模型请求。桥接代码可用 `recorder.model/async_model` 包装其他原生模型调用，用 `recorder.tool/async_tool` 包装真实工具；显式模型包装与 HTTP 观察不重复计数。

SDK `usage`、`usage_metadata`、`token_usage` 中受支持的字段可采集。流式调用、其他网络库、后台线程/子 Agent、未包装的工具可能没有记录。覆盖默认 `partial`，已采集 Token 显示 `≥数值`；没有数据保持未知，费用不凭 Token 估算。工具不存在于轨迹不能确认它没有被调用。接入失败同样保留已有模型/工具记录。

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
