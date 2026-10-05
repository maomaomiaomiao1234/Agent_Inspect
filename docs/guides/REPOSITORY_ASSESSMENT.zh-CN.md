# 从仓库自动部署并评测 Agent

仓库评测现在可以完成：拉取源码、固定提交、读取部署配置、Docker 构建、健康检查、执行固定题集、导出证据和回收资源。提供公开 GitHub HTTPS 地址即可自动接入 Hugging Face smolagents；其他仓库优先读取部署清单，未知 Python 仓库可调用 LLM 自动生成 target-v1 适配器并进行接入检查。完整流程与限制见 [自动适配指南](AUTO_ADAPTATION.zh-CN.md)。

首版支持 Linux/macOS 上的本机 Git 与 Docker daemon。私有仓库、SSH URL、子模块自动初始化、LFS 下载和符号链接构建输入尚未支持；不执行 README 安装指令。构建使用 Docker，依赖下载保留网络；这不是运行任意恶意代码的强隔离平台，公开多人服务还需要专用构建主机及网络/磁盘配额。

## 直接运行 smolagents

### 网页直接运行真实模型评测

在项目根目录的 `.env` 填好 API 地址、模型名和密钥，启动 Docker，然后执行：

```sh
uv run agent-review serve
```

打开 `http://127.0.0.1:8765/#/assessments`，在「从仓库自动部署并评测」填写 `https://github.com/huggingface/smolagents`，点击「拉取、部署并评测」。无需手动启动被测 Agent、登记 targets.json、输入密钥或填写模型变量映射。服务自动读取 `.env`，默认选择真实模型并完成拉取、构建、运行与回收；真实调用使用所配置模型的额度。

模型配置按完整组选择，不跨组混用：

1. 优先专用 `AGENT_REVIEW_TARGET_API_URL`、`AGENT_REVIEW_TARGET_MODEL`、`AGENT_REVIEW_TARGET_TOKEN`。
2. 未设置专用组时，使用有密钥的 `SMOL_MODEL_API_BASE`、`SMOL_MODEL_ID`、`SMOL_MODEL_API_KEY`。
3. SMOL 未提供密钥时，复用 `AGENT_REVIEW_LLM_API_URL`、`AGENT_REVIEW_LLM_MODEL`、`AGENT_REVIEW_LLM_TOKEN`。

因此已有评审模型配置时，只需这一组：

```dotenv
AGENT_REVIEW_LLM_API_URL=https://api.deepseek.com
AGENT_REVIEW_LLM_MODEL=你的模型名
AGENT_REVIEW_LLM_TOKEN=你的密钥
```

URL 用纯文本，不写 Markdown 链接。配置缺失或格式无效时页面提示缺少的变量，禁止提交真实模型任务；不静默转为 offline。修改 `.env` 后重启服务。进程环境优先于 `.env`，显式 CLI 参数优先于启动配置。配置就绪只证明三项格式齐全，实际模型可用性由评测验证。

网页可修改：仓库链接、版本、真实/离线模式、案例数、种子、每案例期限、输出 Token 上限、重复次数、并发、题集策略及清单路径。生成题集的参数进入实际执行和导出证据；上传独立题集时保留文件里的预算、重复次数和并发参数。累计期限上限为 900 秒，多轮/记忆/隔离案例仍串行。

本机 `serve` 默认启用仓库构建，可用 `.env` 的 `AGENT_REVIEW_ENABLE_REPOSITORY_BUILDS=false` 或 `--disable-repository-builds` 关闭。默认参数及 Host/端口见 `.env.example`。远程监听仍需服务访问令牌；直接调用 `create_app` 时构建仍默认关闭。

固定自动配方仍只有 smolagents；其他 Agent 可提供部署清单和 `target-v1` 入口，也可尝试新增的 Python LLM 自动适配。清单声明的 `SMOL_MODEL_*`、`AGENT_REVIEW_TARGET_*` 或 `OPENAI_BASE_URL/OPENAI_MODEL/OPENAI_API_KEY` 自动绑定所选模型配置，仅注入目标明确要求的变量。其他变量可在 `.env` 的 `AGENT_REVIEW_REPOSITORY_ENV` 中允许名称，再在网页高级接入中映射。本机密钥值只在运行容器时注入，不进入构建、浏览器或报告。

### CLI 直接运行（可选）

先确认 `git --version` 和 `docker version` 正常。在项目安装完成后执行：

```sh
uv run agent-review assess-repo https://github.com/huggingface/smolagents \
  --data-dir ./review-data-repository --output ./repository-result.json
```

默认识别仓库、拉取 HEAD，按 seed=42 生成 12 题，构建实际 smolagents 框架和本项目适配器，执行 offline 校准并保存报告。无需手工 clone、启动服务或登记 targets.json。offline 使用脚本模型，记录为 demo，不代表真实大模型成绩。首次运行会下载基础镜像及 Python 依赖。

可用固定提交和不同案例数复现：

```sh
uv run agent-review assess-repo https://github.com/huggingface/smolagents \
  --ref 12c1bc820eca50ace6f80a21d90426d41d74f845 --cases 7 --seed 42 \
  --data-dir ./review-data-repository --output ./repository-result.json
```

CLI 退出码表示编排是否成功完成，题目是否通过应读取报告内 assessment.job.curves/results。错误答案不会使 CLI 假装部署失败；执行失败、取消及清理失败返回非零。

## 网页入口

在装有 Git、Docker 的宿主机启动：

```sh
uv run agent-review serve --data-dir ./review-data-repository \
  --enable-repository-builds --port 8765
```

进入「主动评测」的「从仓库自动部署并评测」，填地址后点击「拉取、部署并评测」。可在高级配置中指定版本、清单路径、独立题集、生成案例数和种子。任务列表显示阶段、失败原因、日志、取消入口和结果下载。

本机 CLI `serve` 默认启用仓库构建；直接调用 `create_app` 或非本机监听时由启动配置明确启用。已有服务令牌和 `X-Review-Request` 边界继续生效；非回环监听需要服务令牌。现有 Compose 服务容器没有 Docker CLI/socket，不能直接启动仓库构建，请使用宿主机 CLI 服务或自行提供受控构建环境。

## 为其他仓库添加部署清单

在被测仓库根目录提交 `agent-review.json`：

```json
{
  "manifest_version": "agent-review/repository-v1",
  "context": ".",
  "dockerfile": "Dockerfile",
  "port": 9000,
  "task_path": "/task",
  "health_path": "/health",
  "user": "65534:65534",
  "memory_mb": 512,
  "cpus": 1,
  "required_environment": [],
  "demo": false
}
```

`context` 相对仓库根目录，`dockerfile` 相对 context。目标须监听容器内 `0.0.0.0:port`，默认健康响应为 `{"status":"ok"}`；可配置 `health_status_field/value`。任务请求和响应遵循 [target-v1](ACTIVE_ASSESSMENT.zh-CN.md#3-被测-agent-的-http-协议)，完整历史支持 JSON 内容。

如目标支持 Bearer 服务令牌，可设置 `service_token_variable` 为目标读取的容器变量名。评审器会为每次部署生成临时令牌，传给容器并用于健康/任务请求，报告中不保存令牌。

清单不接受任意宿主机命令、挂载、privileged 或标准答案文件。目标必须能以非 root 用户、只读根目录运行，可写目录为有界 `/tmp`，限制 CPU/内存/PID。Dockerfile 声明 VOLUME 会被拒绝。

其他任务应由评审者提供独立题集：

```sh
uv run agent-review assess-repo https://github.com/owner/agent \
  --suite ./independent-suite.json --data-dir ./review-data-repository
```

确实满足本项目算术、保存/读取代码工具及 JSON 输出约定的目标，可在清单增加 `"test_template":"smolagents"`，无需手工上传题集。答案由评审端程序计算，清单与 README 的能力声明不会自动充当正确答案。未知 Python 仓库缺少清单时默认尝试 LLM 生成桥接代码；宿主机不执行 README 安装命令。关闭自动适配时沿用缺清单错误。

当前项目的配置示例在 `examples/smolagents/agent-review.json` 与同目录 Dockerfile。该清单的 context 相对整个 Agent_Inspect 仓库，指定 `--manifest examples/smolagents/agent-review.json` 使用。

## 真实模型与环境变量

真实模型的地址、模型名和密钥放在启动评审器的环境变量中，使用名称映射；不要把值写入清单或网页。

```sh
uv run agent-review assess-repo https://github.com/huggingface/smolagents \
  --ref 12c1bc820eca50ace6f80a21d90426d41d74f845 --backend openai \
  --env SMOL_MODEL_API_BASE=MY_MODEL_API_BASE \
  --env SMOL_MODEL_ID=MY_MODEL_ID \
  --env SMOL_MODEL_API_KEY=MY_MODEL_API_KEY \
  --data-dir ./review-data-repository --output ./real-result.json
```

网页/API 自动允许所选模型配置的三个宿主变量；上面的自定义 MY_MODEL_* 名称仍需要管理员允许，可以使用 `.env` 的 `AGENT_REVIEW_REPOSITORY_ENV` 或 `--repository-env`。仓库清单只能声明所需的容器变量名；服务端按已配置的模型别名映射或明确白名单选择宿主变量。凭据仅用于运行目标，不传入 Git、Docker build 参数或构建上下文。

自报 Token 有缺失时完整总量保持 unknown，页面同时显示 `≥已知消耗`；已知消耗仍扣减预算，已知下限超额时明确标为 fail，并停止后续轮次。离线校准显示 Token 不适用。网页默认使用 `.env` 的真实模型配置；CLI `assess-repo` 默认仍为 offline，直接使用 CLI 真实评测时指定 `--backend openai`。HTTP 超时不保证远端模型立即停止计费。

## 状态、恢复与证据

阶段包括 queued、fetching、inspecting、building、assessing、cleaning、finished；自动适配另有 adapting、repairing、validating。每个任务保存 commit、构建输入哈希、实际 image ID、独立 Suite 哈希、日志和 assessment_id。自动适配保存源码片段、生成文件、失败修复记录、供应商返回用量和独立接入检查。源码与镜像关联标为 `built_from_checkout`，表示本机观察到这次构建；基础镜像和外部依赖仍由 Dockerfile 决定，不认证上游签名，也不保证可变依赖下字节级重建。

```sh
uv run agent-review repository-jobs --data-dir ./review-data-repository
uv run agent-review repository-report repository_job_ID \
  --data-dir ./review-data-repository --output ./repository-bundle.json
uv run agent-review repository-cancel repository_job_ID --data-dir ./review-data-repository
```

同一 data-dir 只能有一个评审执行进程，由进程锁保证。运行网页服务时通过网页/API 提交，CLI 单独执行请选独立目录。报告与取消命令可读取运行中服务的数据。

容器身份在启动前写入数据库并加所有者标签；正常退出会清理，服务异常退出后的下次启动会回收遗留容器及临时凭据文件，把旧任务标为 interrupted，不重放付费请求。Git/build 客户端由独立看守进程监控，评审进程消失后会终止该命令进程组。失败清理保留记录，下次启动重试；所有者不匹配时不删除容器。任务结束删除 checkout/构建上下文及本次镜像标签，保留证据；共享基础镜像与 Docker 构建缓存不做全局清理。

拉取阶段每条命令最多 120 秒；源码限制 200 MiB/20,000 文件，能力扫描另沿用 5,000 文件限制；构建最多 600 秒，单条命令日志最多 1 MiB；队列最多 8 项，并复用现有两个评测 worker。取消会终止 Git/build 客户端并清理临时资源，评测中的 HTTP 调用仍受每案例期限限制。构建资源上限还取决于本机 Docker 配置；构建缓存与磁盘配额需由宿主环境管理。

API：`GET /api/repository-builds` 返回启用状态和环境变量允许列表；`POST/GET /api/repository-jobs` 提交/列出任务；`GET /api/repository-jobs/{id}` 查看详情；`POST .../{id}/cancel` 取消；`GET .../{id}/export` 导出完整证据。请求及清单 schema 位于 `/api/schema`。

`GET /api/repository-jobs/{id}/adapter-files` 下载最近一轮生成的接入文件 ZIP，包含验证状态和固定提交，原仓库源码另行取得。失败文件不能视为已通过接入检查。完整 JSON 导出中的 `adaptation`、`adapter_validations` 和 `assessment` 分别对应生成、接入检查与正式评测。
