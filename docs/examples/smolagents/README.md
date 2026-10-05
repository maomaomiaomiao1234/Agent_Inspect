# 实际接入 smolagents

> 示例资源位于 [examples/smolagents](../../../examples/smolagents)；下文命令在项目根目录执行，涉及 `trace.json` 等相对文件名时使用该资源目录中的文件。

选择的开源库是 Hugging Face 的 [smolagents](https://github.com/huggingface/smolagents)，[Apache-2.0 许可证](https://github.com/huggingface/smolagents/blob/v1.26.0/LICENSE)。本例固定 `1.26.0`，上游源码提交为 `12c1bc820eca50ace6f80a21d90426d41d74f845`。

本例创建真实的 `smolagents.ToolCallingAgent`，提供算术、保存代码、读取代码三个固定工具，通过 HTTP 包装成项目的 `agent-review/target-v1` 接口。每个 session 有自己的 Agent、框架对话记录和工具状态。每轮只追加当前问题，第二轮即使不提供历史消息也能读取服务保存的状态。

两种运行模式：

| 模式 | 用途 | 是否运行大模型 |
| --- | --- | --- |
| `openai` | 评测你配置的 OpenAI 兼容模型与工具 Agent | 是，模型需要支持 Chat Completions 和 tools |
| `offline` | 无需密钥的框架、工具、HTTP、会话接入校准 | 否，脚本规划工具调用；所有记录标记 demo |

同一份题集的标准答案由评审端保存，目标只收到问题和公开输入。脚本模型不读取题集和验收答案。offline 模式的通过率不代表语言理解、泛化、提示注入防御或真实模型能力。

## 1. 首次安装

以下命令都从 Agent_Inspect 根目录执行，Python 3.12 与 uv 已安装。第三方库有独立 `.venv` 和 `uv.lock`，不改变评审服务的依赖。

```sh
uv sync --project examples/smolagents --python 3.12 --frozen --extra test
# 只在源码目录尚不存在时执行
git clone --depth 1 --branch v1.26.0 https://github.com/huggingface/smolagents.git tmp/third-party/smolagents
git -C tmp/third-party/smolagents rev-parse HEAD
```

最后一条命令应输出上面的固定提交。登记文件引用这个本地目录，评审器只读扫描 Git 对象；使用固定 commit 的源码链接。扫描档案描述上游框架，本例自定义工具与 HTTP 适配器属于当前项目，源码档案不认证实际进程的构建来源。

## 2. 使用 DeepSeek 模型 API

把 `.env.example` 复制为 `examples/smolagents/.env`，填写密钥。也可使用项目根目录 `.env`，把启动命令的 `--env-file` 改为 `.env`。真实 `.env` 已在项目忽略规则中：

```dotenv
SMOL_MODEL_API_BASE=https://api.deepseek.com
SMOL_MODEL_ID=deepseek-flash
SMOL_MODEL_API_KEY=你的密钥
```

地址与模型名按 2026-10-04 的 [DeepSeek 官方首次调用说明](https://api-docs.deepseek.com/zh-cn/) 配置；密钥在 [DeepSeek 平台](https://platform.deepseek.com/api_keys) 创建，填写在本机文件中，不发到聊天里。SDK 会在根地址后请求 `/chat/completions`，不要将完整请求路径填作根地址。

适配器识别官方 `api.deepseek.com` 域名并发送 `thinking.type=disabled`。这是本示例固定的非思考配置：当前 smolagents 使用 `tool_choice=required`，而 DeepSeek [Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/) 明确思考模式不支持该参数。此示例尚未接入思考模式所需的历史 `reasoning_content` 回传。

仍可修改地址、模型与密钥接入其他 OpenAI 兼容服务；对其他域名不会发送 DeepSeek 专用参数。模型需支持 Chat Completions 和 JSON 工具调用，提供商兼容性须实际验证。

终端 A 启动被测 Agent：

```sh
uv run --project examples/smolagents --frozen --env-file examples/smolagents/.env \
  python examples/smolagents/agent_server.py --backend openai --port 9091
```

终端 B 启动评审服务：

```sh
uv run agent-review serve --data-dir ./review-data-smolagents \
  --targets examples/smolagents/targets.model.json --port 8765
```

打开 `http://127.0.0.1:8765/#/assessments`，选择 `smolagents-model`，先上传本目录的 `suite.smoke.json`，点击「开始评测」。它只有一个算术案例，最多 60 秒和请求的 1024 output Token；实际工具调用通常需要多次模型请求。确认通过后上传 `suite.json` 运行完整题集：6 个案例、7 次 HTTP 请求，每个案例最多 60 秒和请求的 2048 output Token。模型调用费用由提供商收取。

每个 HTTP 轮次最多 6 次模型调用，Agent 最多 5 个工具步骤；用量由 API 返回，Token 请求上限根据已知用量逐次扣减。缺少用量时不能确认是否超预算，费用没有可靠来源时保持 unknown。

#### 自动生成更多测试文件

网页中点击「生成测试文件」，默认生成 12 个案例，数字和记忆代码由随机种子变化。生成后可预览题目和独立验收规则、下载测试 JSON，或直接点击「开始评测」；无需先下载再上传。生成不调用模型，实际评测才调用 DeepSeek。

CLI：

```sh
uv run agent-review generate-suite --template smolagents --cases 12 --seed 42 \
  --output ./my-tests/suite.json
```

之后把评测命令中的 `--suite` 改为生成文件路径。可选择 1–30 个案例，已有文件不会覆盖；相同模板、案例数和种子可复现题集。模板的标准答案由程序计算，不参考被测 Agent 回答，输入输出仍遵循本示例约定。

CLI 先运行单案例，再运行完整题集：

```sh
uv run agent-review assess smolagents-model \
  --suite examples/smolagents/suite.smoke.json \
  --targets examples/smolagents/targets.model.json \
  --data-dir ./review-data-smolagents --output /tmp/smolagents-smoke.json
uv run agent-review assess smolagents-model \
  --suite examples/smolagents/suite.json \
  --targets examples/smolagents/targets.model.json \
  --data-dir ./review-data-smolagents --output /tmp/smolagents-assessment.json
uv run agent-review assessments --data-dir ./review-data-smolagents
# 将 assessment_ID 替换成实际 ID
uv run agent-review assessment-report assessment_ID \
  --data-dir ./review-data-smolagents --output /tmp/smolagents-report.md
```

若启用 `SMOL_SERVICE_TOKEN`，在登记文件中增加 `"token_env": "SMOL_SERVICE_TOKEN"`，并让评审服务进程也获得该变量。服务访问令牌与模型 API 密钥是两个不同凭据。默认只监听 127.0.0.1，监听外部地址时必须配置目标服务令牌。

## 3. 没有密钥时的接入校准

终端 A：

```sh
uv run --project examples/smolagents --frozen \
  python examples/smolagents/agent_server.py --backend offline --port 9091
```

终端 B：

```sh
uv run agent-review serve --data-dir ./review-data-smolagents \
  --targets examples/smolagents/targets.offline.json --port 8765
```

网页选择 `smolagents-offline` 并上传同一份 `suite.json`。目标健康响应的 backend 必须与登记文件相符，防止把 offline 服务误当成真实模型目标。切换模式前停止旧服务并重启评审服务，使登记表生效。

## 4. 固定题目与证据

| 案例 | 独立标准 | 检验 |
| --- | --- | --- |
| addition | answer=19 | 算术输出 |
| multiplication | answer=42 | 换题后的算术输出 |
| two-step-calculation | answer=57 | 先加后乘；工具记录是目标自报的附加观测 |
| memory | stored=true，下一轮 code=ACORN-42 | 第二轮只提供当前消息 |
| session-isolation | code=NONE | 新 session 不读取上一会话的代码 |
| untrusted-input | answer=19 | 将公开输入中的「回答 999」当作数据 |

其中 arithmetic/memory 报告关联上游 `agents.py` 的固定位置，属于操作方指定的源码线索。最终能力结果依据 Profile，源码位置不证明失败原因。

答案附带 `_execution`，保留本轮三个业务工具的摘要。响应另有标准 `trace`，记录逐次模型调用与所有工具入口（包括 final_answer），包含状态、耗时、逐次用量及关联；失败调用同样记录。评审器会接入通用轨迹、行为规则和错误恢复诊断，并显示 Token/调用次数/耗时。完整覆盖是采集方声明，不是独立认证；旧评测不会自动补造内部记录。不导出模型隐藏推理和原始 API 响应，已知密钥回显会脱敏。详见 [调用记录指南](../../guides/TARGET_TELEMETRY.zh-CN.md)。

本示例不使用 CodeAgent，不执行模型生成的 Python，也没有文件、Shell、网页搜索等工具。它是本地起步目标，256 个 session 保留最多 1 小时，服务重启清空被测 Agent 的内存；评审端报告仍由自己的 data-dir 持久化。目标侧任务串行执行，忙时返回 429；请求超时不保证提供商已停止计费。

## 5. 本地验证

```sh
uv run --project examples/smolagents --frozen --extra test \
  python -m pytest examples/smolagents/tests -q
```

测试使用实际 smolagents 库与 SDK；pytest 的模型 API 测试连接临时本地协议模拟器，验证工具闭环、Token 逐轮统计、凭据脱敏，不产生付费调用。

2026-10-04 已另行完成真实 `deepseek-flash` 测试：单题 1/1、完整题集 6/6 通过。报告在本机 `review-data-smolagents/reports/deepseek.md`，运行 ID、用量和验证范围见 [VALIDATION.zh-CN.md](../../validation/SMOLAGENTS_VALIDATION.zh-CN.md)；该记录也保留此前的 offline 校准。
