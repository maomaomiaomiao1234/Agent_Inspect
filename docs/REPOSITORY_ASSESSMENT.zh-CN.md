# 从仓库自动部署并评测 Agent

仓库评测现在可以完成：拉取源码、固定提交、读取部署配置、Docker 构建、健康检查、执行固定题集、导出证据和回收资源。提供公开 GitHub HTTPS 地址即可自动接入 Hugging Face smolagents；其他仓库需要提供部署清单和符合 target-v1 的 HTTP 服务。

首版支持 Linux/macOS 上的本机 Git 与 Docker daemon。私有仓库、SSH URL、子模块自动初始化、LFS 下载和符号链接构建输入尚未支持；不执行 README 安装指令。构建使用 Docker，依赖下载保留网络；这不是运行任意恶意代码的强隔离平台，公开多人服务还需要专用构建主机及网络/磁盘配额。

## 直接运行 smolagents

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

仓库构建默认关闭，管理员显式启用后才可通过 API/网页触发代码构建。已有服务令牌和 `X-Review-Request` 边界继续生效；非回环监听需要服务令牌。现有 Compose 服务容器没有 Docker CLI/socket，不能直接启动仓库构建，请使用宿主机 CLI 服务或自行提供受控构建环境。

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

确实满足本项目算术、保存/读取代码工具及 JSON 输出约定的目标，可在清单增加 `"test_template":"smolagents"`，无需手工上传题集。答案由评审端程序计算，清单与 README 的能力声明不会自动充当正确答案。未知仓库缺少清单时会返回明确错误，不猜测可执行安装命令。

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

网页/API 使用的宿主变量必须另外由管理员逐个允许：`serve --enable-repository-builds --repository-env MY_MODEL_API_BASE --repository-env MY_MODEL_ID --repository-env MY_MODEL_API_KEY`。仓库里的清单只声明所需的容器变量名，无权选择读取评审服务凭据。凭据仅用于运行目标，不传入 Git、Docker build 参数或构建上下文。

自报 Token 有缺失时总量保持 unknown，但已知消耗仍扣减预算；已知下限超额时明确标为 fail，并停止后续轮次。HTTP 超时不保证远端模型立即停止计费。

## 状态、恢复与证据

阶段依次为 queued、fetching、inspecting、building、assessing、cleaning、finished。每个任务保存 commit、构建输入哈希、实际 image ID、独立 Suite 哈希、日志和 assessment_id。源码与镜像关联标为 `built_from_checkout`，表示本机观察到这次构建；基础镜像和外部依赖仍由 Dockerfile 决定，不认证上游签名，也不保证可变依赖下字节级重建。

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
