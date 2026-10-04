# 主动评测其他 Agent

这条流程会向被测 Agent 的 HTTP 服务实际发送任务，保存对话，再用评审端的固定 Profile 检查输出。仓库扫描提供能力声明与源码位置，任务执行提供观测结果，两者会关联到同一份报告。

当前实现是确定性的评测编排服务。现在支持 [从仓库自动部署并评测](REPOSITORY_ASSESSMENT.zh-CN.md)：公开 GitHub 上的 smolagents 或带部署清单的目标可自动接入。比赛题集和标准答案仍由评审方固定；已有服务通过下面的协议接入。

## 1. 先跑通控制 Agent

按照根 README 安装依赖、构建网页，然后打开两个终端。

终端 A 启动内置 HTTP 控制 Agent：

```sh
uv run python examples/assessment/mock_agent.py --port 9081
```

终端 B 启动评审服务并登记目标：

```sh
uv run agent-review serve \
  --data-dir ./review-data \
  --targets examples/assessment/targets.json \
  --port 8765
```

打开 `http://127.0.0.1:8765/#/assessments`，选择 `control`，上传 `examples/assessment/suite.json`，点击「开始评测」。题集有 4 个案例、2 个预算、每项重复 2 次，共 16 个评测结果。记忆案例的第二轮只发送当前问题，检验目标是否保留了同一 session 的状态。

| 目标 | 预期结果 | 用于检查 |
| --- | --- | --- |
| control | 16 通过 | 正常执行和独立验收 |
| incorrect | 16 失败 | 错误答案不能通过 |
| noop | 16 失败 | 空对象不能代替任务产物 |
| missing | 16 证据不足 | 自报完成不能代替有效输出 |
| leaky | 12 通过、4 失败 | 跨 session 的记忆泄漏 |

这些目标是确定性控制程序，没有运行大模型。结果用于校准评测流程，不代表真实 Agent 的能力。它们的用量保持未知。

CLI 也可以发起评测：

```sh
uv run agent-review assess control \
  --suite examples/assessment/suite.json \
  --targets examples/assessment/targets.json \
  --data-dir ./review-data \
  --output /tmp/control-assessment.json
uv run agent-review assessments --data-dir ./review-data
# 用上一条命令显示的 assessment_ID 替换下面的 ID
uv run agent-review assessment-report assessment_ID \
  --data-dir ./review-data --output /tmp/assessment.md
```

`assess` 的退出码表示编排是否完成，能力是否通过应读取每个案例的 `outcome`；一个全部答错、但正常响应的 Agent 仍可完成整个评测。网页详情可查看单个案例的对话、验收、源码线索，下载报告与完整证据包。

### 自动生成测试文件

使用 [smolagents 示例](../examples/smolagents/README.md) 时，无需手写所有题目。网页的新建评测区提供「自动生成测试文件」，配置 1–30 个案例和种子后，生成的题集自动成为当前评测输入。可以预览验收规则、下载保存，也可以直接开始评测。

```sh
uv run agent-review generate-suite --template smolagents --cases 12 --seed 42 \
  --output ./my-tests/suite.json
```

生成器版本为 `smolagents-v1`，按种子变化数字与记忆代码，交替生成加法、减法、乘法、两步工具调用、两轮记忆、隔离及干扰题。标准答案由程序独立计算，生成过程不访问被测目标或模型。生成的 JSON 遵守既有 assessment-v1 协议，默认每案例期限 30 秒、请求最多 2048 output Token；生成后在提交时冻结题集，改变种子会改变题目，不能直接作为相同题集的版本回归。

生成端 API：`POST /api/assessment-suites/generate`，body 为 `{"template":"smolagents","cases":12,"seed":42}`，响应为可保存的 Suite JSON。该接口沿用服务认证及 `X-Review-Request: 1`，仅生成数据，不提交任务。请求合同也由 `/api/schema` 的 `suite_generation_input` 提供。

此模板要求目标提供算术、保存代码、读取代码工具，以及 `/output/answer`、`/output/stored`、`/output/code` 输出约定。它不会读取任意仓库来猜测输入输出接口；其他 Agent 需要自己的模板或独立验收标准。生成的答案不发送给目标，工具步数材料仍为目标自报；固定干扰题通过不代表全面的提示注入防御能力。

## 2. 登记真实目标与仓库

目标登记文件由服务管理员管理，格式为 JSON 数组。API 用户只能选择登记的 `target_id`，不能通过请求覆盖 URL、仓库路径或 Docker 配置。目标登记变更后重启服务。

```json
[
  {
    "id": "my-agent",
    "endpoint": "http://127.0.0.1:9000",
    "task_path": "/task",
    "health_path": "/health",
    "token_env": "MY_AGENT_SERVICE_TOKEN",
    "repository": "/absolute/path/to/cloned-agent",
    "ref": "REPLACE_WITH_A_FIXED_COMMIT",
    "repository_url": "https://github.com/owner/repository",
    "demo": false
  }
]
```

没有目标鉴权时省略 `token_env`。凭据通过对应环境变量提供，登记文件不接收凭据值。`endpoint` 是服务根地址，不能含用户名、密码、query 或路径；任务与健康检查路径单独配置。健康检查默认要求 2xx JSON 中 `status` 等于 `ok`，可设置 `health_status_field` 和 `health_status_value` 适配其他服务。

使用已有服务登记时，仓库应先由管理员克隆；使用仓库自动评测入口时由流水线拉取并固定提交。这里的扫描器只读取本地 Git 对象，不执行代码、初始化子模块或采用未提交改动。没有 `.git` 的目录可生成内容快照，`commit` 保持空，不能将它视为某个已部署版本。

```sh
uv run agent-review repo-inspect /absolute/path/to/cloned-agent \
  --ref COMMIT_SHA \
  --repository-url https://github.com/owner/repository \
  --data-dir ./review-data --output /tmp/repository-profile.json
```

档案包含 README 中的能力声明、依赖/入口/环境变量/路由线索、文件哈希和行号。关键词扫描可能漏掉或误识别能力，静态内容不证明运行能力。扫描最多 5000 个仓库文件、单个文本文件 256 KiB、总文本 10 MiB；超大、二进制、符号链接、子模块、敏感目录和真实 `.env` 文件会被跳过或触发限制。

## 3. 被测 Agent 的 HTTP 协议

首版协议为 `agent-review/target-v1`，需要在目标服务中提供适配入口。如果目标目前使用 A2A、OpenAI Chat、CLI 或其他 SDK，可在目标一侧写一个薄适配层。当前项目没有宣称兼容这些协议的所有功能。

任务请求示例：

```json
{
  "protocol": "agent-review/target-v1",
  "session_id": "a-unique-session-id",
  "turn": 0,
  "prompt": "Compute 12 + 7. Return JSON with the field answer.",
  "messages": [{"role": "user", "content": "Compute 12 + 7. Return JSON with the field answer."}],
  "input": {},
  "budget": {"id": "small", "deadline_seconds": 3, "max_output_tokens": 32}
}
```

目标返回：

```json
{
  "protocol": "agent-review/target-v1",
  "output": {"answer": 19},
  "usage": {"tokens": {"input": 20, "output": 8, "total": 28}, "cost_usd": 0.0001}
}
```

`output` 必须存在，可为合法 JSON 值。`usage` 可省略；提供时必须是有限非负数，Token 必须为整数。示例中的用量仅展示格式。评审端将其标记为目标自报，不当作供应商账单。目标响应包含协议未声明的额外字段时会被拒绝。

每个 case × budget × attempt 使用新的 session_id，同一 case 的多轮请求使用相同 ID。`history: "full"`（默认）发送累计可见对话；`history: "current"` 只发送本轮用户消息，适合检验服务内的记忆。`input` 用于题集提供的公开 JSON 输入材料；评审端不会把 Profile、标准答案、源码线索或其他 session 的消息放进请求。

目标必须自行把 `session_id` 关联到其会话。HTTP 客户端不跟随重定向、不继承宿主机代理、不自动重试；单次请求最多 128 KiB、响应最多 256 KiB。支持未压缩 JSON，拒绝其他 Content-Encoding。取消、超时、无效 JSON、缺少有效协议或凭据错误会保留执行状态和已有对话，不能据此确认能力通过。

## 4. 配置题集、独立验收和源码关联

完整可运行样例在 `examples/assessment/suite.json`。最小题集：

```json
{
  "suite_version": "agent-review/assessment-v1",
  "id": "addition",
  "version": "1",
  "attempts": 2,
  "budgets": [{"id": "default", "deadline_seconds": 10}],
  "cases": [{
    "id": "sum", "category": "capability",
    "turns": [{"prompt": "Compute 12 + 7. Return JSON with the field answer."}],
    "profile": {
      "profile_version": "1", "id": "sum-check",
      "rules": [
        {"id": "present", "op": "exists", "path": "/output/answer"},
        {"id": "correct", "op": "equals", "path": "/output/answer", "value": 19}
      ]
    }
  }]
}
```

Profile 沿用项目现有声明式规则。最终答案在 `/output`；第 1 轮答案在 `/artifacts/assessment/turns/0/output`；公开输入与观测信息在对应的 `assessment` 快照中。字段必须存在时同时使用 `exists`，避免缺少字段仅变成 unknown。`external` 规则在本流程中没有提交独立结果时保留 unknown，不会执行来自题集的命令。

`category` 支持 `capability`、`multi_turn`、`robustness`、`memory`、`session_isolation`，它只是分类，不会自动添加该维度的测试。要评价故障恢复或抗提示注入，需要在题目中设计可复现的故障或干扰，并配置独立验收；当前没有自动向第三方工具服务器注入故障的通用模块。

把档案中的 `claim_id` 放入某个 case 的 `claim_ids`，可以得到 `supported_for_cases`、`contradicted_by_cases`、`inconclusive` 或 `untested` 的声明验证状态。`source_refs` 可指定源码定位线索：

```json
{
  "claim_ids": ["claim_ID_FROM_REPOSITORY_PROFILE"],
  "source_refs": [{"path": "agent.py", "line": 25}]
}
```

这些字段加在 case 中。评测开始前会检查 claim、文件和行号属于当前扫描快照。它们保留出处和固定 commit 链接，不证明代码是失败的原因。

评测记录绑定 Suite 哈希、执行器代码哈希、目标配置哈希、仓库 commit 和扫描内容哈希。通过率按全部计划案例计算，未知和未完成案例保留在分母。预算曲线提供每档通过/失败/未知、观测耗时及自报资源；缺失资源不填零。

`deadline_seconds` 是每个多轮案例的总 HTTP 执行期限，后续轮次得到剩余时间。Token 是向目标请求的约束；已收到自报 output Token 时会扣减后续轮次预算，耗尽则停止继续发题。缺少用量时不能确认是否超限，也不能强制限制目标内部的供应商账单。

题集最多 30 个案例、5 档预算、10 次重复；单次评测最多 300 个 HTTP 请求和 900 秒累计案例期限。单个 API/CLI 题集文件最多 1 MiB。官方能力排名应使用对所有参赛目标一致的冻结题集；本项目未接入 SWE-bench、Harbor 或 OmniDocBench 官方评分。

## 5. 异步任务、取消与版本回归

服务同时执行最多 2 个评测，排队与执行中的任务合计最多 32 个。任务与逐案例结果存储在 SQLite，工件存储在同一 data-dir 的内容寻址目录。

状态包括 `queued`、`running`、`completed`、`failed`、`cancelled`、`interrupted`。取消不会抹掉已收集的证据；正在等待的 HTTP 调用会在当前期限内退出。取消 HTTP 等待不保证已部署目标的内部作业停止；受管理的 Docker 目标会在清理时停止容器。服务重启后，旧的 queued/running 任务标为 interrupted，已有结果保留，不会自动重放可能产生费用的请求。

当前使用单个服务进程和进程内线程池，部署时使用项目 CLI，不配置多个 Uvicorn worker，也不要让多个服务共享同一个 data-dir。需要分布式队列、租户隔离和计费配额时，应继续扩展这一层。

```sh
uv run agent-review assessment-cancel assessment_ID --data-dir ./review-data
uv run agent-review assessment-compare assessment_BASELINE assessment_NEW --data-dir ./review-data
uv run agent-review repo-compare repo_BASELINE repo_NEW --data-dir ./review-data
```

回归比较要求同一 target_id、同一 Suite 哈希和执行器哈希，两次任务均 completed，且示例标记一致。报告给出逐案例结果变化，`pass → fail` 标记回归。目标配置发生变化会单独披露；源码与实际服务的绑定尚未认证，不能把观测变化直接归因于某一行代码。重复测量提供稳定性观测，目前没有统计显著性检验或置信区间。

## 6. 服务 API 与访问令牌

写请求保留 `X-Review-Request: 1`。它用于本地请求边界，不是身份认证。设置 `AGENT_REVIEW_SERVICE_TOKEN` 后，除 `/api/health` 外的 API（包括报告和工件）都要求 `Authorization: Bearer ...`，网页会先显示访问令牌输入页。令牌只留在页面内存中，刷新后重新输入。

CLI 监听非回环地址时要求配置服务令牌；未配置令牌时，应用也拒绝远程 API 客户端。通过域名访问时，在 `AGENT_REVIEW_ALLOWED_HOSTS` 中加入该域名。远程公开服务应通过 HTTPS 反向代理提供访问；本项目未实现多个用户账号或租户隔离。

| API | 用途 |
| --- | --- |
| GET /api/targets | 查看可选目标，隐藏地址和环境配置 |
| POST /api/targets/{id}/repository-profile | 扫描登记的仓库版本 |
| GET /api/repository-profiles/{id} | 查看保存的能力档案 |
| POST /api/repository-comparisons | 对比两个源码档案 |
| POST /api/assessments | 提交 `{target_id, suite}`，返回 202 与 job ID |
| GET /api/assessments?limit=50&offset=0 | 分页查看任务 |
| GET /api/assessments/{id} | 查看状态、进度和逐案例结果 |
| POST /api/assessments/{id}/cancel | 请求取消 |
| GET /api/assessments/{id}/export?format=markdown\|json\|bundle | 导出报告或题集、源码档案、运行包与评估快照 |
| POST /api/assessment-comparisons | 对比 `{left_id, right_id}` |
| GET /api/schema | 查看目标与题集 JSON Schema |

`bundle` 是离线档案，包含 Suite、源码档案、各运行包及评估版本；导出不会重新调用 Agent。目前没有整个评测档案的导入入口；其中的通用运行包可用已有导入命令读取，再按相应 Profile 重评。导入历史材料不认证其执行来源。

## 7. Docker 部署

### 部署评审服务

根 Dockerfile 构建前端并安装 `deploy/requirements.txt` 中从 uv.lock 导出的带哈希依赖。Compose 同时启动评审服务和控制 Agent；首次构建需要下载基础镜像和依赖。

先在当前终端设置一个足够长的随机 `AGENT_REVIEW_SERVICE_TOKEN`，然后运行：

```sh
docker compose -f deploy/compose.yaml up --build -d
```

访问 `http://127.0.0.1:8765/#/assessments`，输入刚配置的令牌，上传示例题集。数据保存在 named volume `review-data`；停止服务使用 `docker compose -f deploy/compose.yaml down`，默认保留数据卷。

Compose 的登记文件使用容器网络内的 `control-agent:9081`。换成真实目标时修改 `deploy/targets.example.json` 或挂载自己的配置文件，保持目标路径与容器内路径一致。可用 Dockerfile 的 `NODE_IMAGE`、`PYTHON_IMAGE` build args 固定基础镜像 digest；默认 tag 不是不可变版本，发布时记录最终镜像 digest。

服务容器无需 Docker socket，可以评测已部署的 HTTP 目标。它没有集成任意仓库构建器。

### 由本机评审器启动被测镜像

已安装 Docker 的宿主机还可以登记 `deployment` 替代 endpoint：

```json
{
  "id": "container-agent",
  "deployment": {
    "image": "sha256:REPLACE_WITH_64_HEX_IMAGE_ID",
    "port": 9000,
    "user": "65534:65534",
    "memory_mb": 512,
    "cpus": 1,
    "environment": {"MODEL_API_KEY": "MY_TARGET_MODEL_KEY"}
  },
  "task_path": "/task",
  "health_path": "/health",
  "demo": false
}
```

目标镜像必须已由管理员构建和安装，使用 `docker image inspect IMAGE --format '{{.Id}}'` 取得固定 ID。评审器不自动 build/pull，不挂载源码、宿主机目录或 socket，也不回退到宿主机执行。镜像声明的 VOLUME 会被拒绝。

每个评测启动独立容器，非 root UID、只读根文件系统、去 capabilities、no-new-privileges、CPU/内存/PID 限制、临时 `/tmp`、随机回环端口；同一评测的多个案例共享这个容器，以便测试 session 隔离。桥接网络保留出站连接，允许目标访问其模型服务；当前没有域名级出站控制。环境映射只转发明确登记的变量，临时凭据文件权限为 600，启动或失败后删除。任务结束、异常或取消均尝试清理本轮容器；无法确认清理时报告准确的容器名。

记录包含固定镜像 ID、启动时间、实际健康响应和执行策略，但镜像与源码提交的构建来源绑定仍未认证。因此它验证「这个镜像能启动并完成这些题目」，不能证明任意开源仓库能自动重建或第三方构建声明可信。

## 8. 验证与后续扩展

```sh
uv run pytest -q
uv run ruff check src scripts tests
uv run python scripts/check_assessments.py --data-dir /absolute/new-test-data
# 可选：先构建 examples/assessment/Dockerfile，然后把实际 sha256 ID 传入
uv run python scripts/check_assessments.py \
  --data-dir /absolute/another-new-test-data --docker-image sha256:IMAGE_ID
```

验证脚本实际启动 HTTP 服务、生成固定 Git 控制仓库、校准五类输出、检验版本回归，可选实际启动 Docker 目标。详细验收记录见 [主动评测验收记录](../ACTIVE_ASSESSMENT_VALIDATION.zh-CN.md)。

下一阶段可按比赛要求增加 A2A 适配、经过独立验收的任务数据集、可重复的源码构建与来源证明、工具故障注入、可信外部 evaluator 执行、分布式队列和统计报告。当前没有自动泛化出题、自动定位故障根因或自动下载完整官方基准。
