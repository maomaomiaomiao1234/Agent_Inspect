# 主动 Agent 评测与服务部署验收

验收日期：2026-10-03。对象是评审服务、固定 HTTP 控制程序和固定题集。控制程序没有调用模型；本轮尚未接入真实第三方 Agent，不形成比赛能力排名。

使用方法见 [README](../../README.md#active-assessment) 和 [主动评测指南](../guides/ACTIVE_ASSESSMENT.zh-CN.md)。

## 1. 本轮交付

- 固定 Git 提交的只读仓库档案：能力声明、入口/依赖/环境线索、文件哈希、行号及源码位置。
- 实际 HTTP 发题与独立 Profile 验收，支持多轮、服务记忆、会话隔离、干扰输入、重复次数和多档预算。
- 声明与案例关联、源码线索、逐案例版本回归、Markdown/JSON/离线证据包。
- SQLite 异步队列、取消、重启中断恢复、访问令牌、网页与 CLI。
- 评审服务 Dockerfile/Compose；可选启动已安装的固定镜像，健康检查并清理目标容器。

源码声明保持「未验证」直到关联案例产生证据。HTTP 观测不补造目标内部工具轨迹；源码位置是定位线索，不是故障因果证明。

## 2. 自动验证

| 检查 | 结果 | 实际范围 |
| --- | --- | --- |
| 全量 Python | 266 passed，2 条已有依赖弃用警告 | 原有流程及新增 41 项主动评测测试 |
| Ruff | 通过 | src、scripts、tests |
| TypeScript/Vite | 构建通过 | 生产网页、访问令牌、任务详情与移动布局 |
| Chrome 全量流程 | 7 passed / 1 skipped | 导入、比较、Profile、代码/文档专项、主动评测；原有 LLM 流程缺少 REVIEW_MOCK_API_URL 而跳过 |
| 带访问令牌的 Chrome 主动评测 | 1 passed | 错误/正确令牌、实际任务、认证导出、手机布局 |
| wheel 独立检查 | 通过 | 在源码之外加载网页、协议、导入与主动评测执行器 |
| skill 分发 | 重建成功；启动器 6 passed | wheel、runtime 和依赖哈希同步；同版本构建以内容哈希区分 |
| Docker 目标执行 | 16/16 pass | 本机评审器启动固定镜像，真实 HTTP 请求、健康检查、最终清理 |
| Docker 评审服务执行 | 16/16 pass | 两个独立容器、认证、只读 Git 仓库扫描、报告/工件导出、重启持久化 |

测试覆盖固定提交与工作区分离、扫描边界、符号链接和凭据脱敏、协议错误、超时与慢速响应、无重试、取消部分结果、预算耗尽、遗漏用量、队列上限、重启、比较限制和容器清理失败。故障分支主要使用单元测试；不声称每个异常都在真实 Docker 中注入。

浏览器截图已检查桌面与手机布局。较宽的预算表在手机上横向滚动，页面本身不溢出。原 `.agent-review/` 未修改，测试使用独立临时数据目录。

## 3. 实际 HTTP 校准

每个场景执行 4 个案例 × 2 档预算 × 2 次重复，共 16 项。案例包括算术、服务内记忆、会话隔离和不可信输入。

| 控制目标 | pass | fail | inconclusive | 说明 |
| --- | ---: | ---: | ---: | --- |
| correct | 16 | 0 | 0 | 按 session 保留状态，返回固定正确答案 |
| incorrect | 0 | 16 | 0 | 协议有效，答案错误 |
| noop | 0 | 16 | 0 | 协议有效，输出为空 |
| missing | 0 | 0 | 16 | 自报完成但缺少有效输出，不能确认能力结论 |
| leaky | 12 | 4 | 0 | 共享全局记忆，四项会话隔离测量失败 |
| Docker correct | 16 | 0 | 0 | 相同控制程序在独立目标容器运行 |

记忆案例的第二轮只发送当前消息，验证目标自身保存的 session 状态。公开输入会发送给目标，标准答案、Profile 与源码关联留在评审端。控制程序未提供 Token/费用，报告保持 unknown。correct → incorrect 的比较实际检出 16 项结果变化；比较同时披露目标路径变化，不能归因于代码提交。

离线证据在 [examples/assessment/verified](../../examples/assessment/verified/summary.json)：六个 bundle 包含题集、源码档案、外部对话运行包和评估快照；summary 记录各 bundle 的 SHA-256、执行器和题集哈希。验证脚本对记忆案例加入声明和源码引用，所以校准题集哈希与未关联源码的原始样例不同。

执行器代码哈希：`c2e40686d6f681ffe414c8f263acf92361c7ca9f1f121565591ec0daec85d419`。

这些文件保存历史观测，不会重新启动目标。当前没有整个主动评测 bundle 的导入入口；其中的 generic 运行包可用现有导入命令读取，必要时按相应 Profile 重评。

## 4. 实际容器部署

本机 Docker Server 29.8.1，实际平台 `linux/arm64`。`linux/amd64` 和云端平台尚未验收。

| 镜像 | 本轮实际 ID |
| --- | --- |
| Python 3.12 slim 基础镜像 | sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f |
| Node 22 alpine 构建镜像 | sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 |
| 控制 Agent | sha256:17e8beaddd7e1ea49d1e612a20be059cf4eea9c7bd9408db957c6aa91f1778b8 |
| 评审服务 | sha256:de095cd30858b7177f2113cd0e9d90f4ef8f491da61d767b5fef250256064b14 |

[服务部署记录](../../examples/assessment/verified/service-deployment.json) 包含实际镜像 ID、平台和任务结果。临时令牌没有写入记录。

验收通过以下实际操作：

1. 启动独立控制 Agent 和评审服务容器，在专用网络内通过 HTTP 交互。
2. 未认证访问 API 返回 401；带令牌完成 16 项测量。
3. 评审容器读取只读挂载的 Git 控制仓库，得到固定 commit 的源码档案。
4. 下载报告、16 个案例运行包和认证工件。
5. 重启评审容器，从数据卷读取相同评测及工件。
6. 清理本轮容器、网络和数据卷；最终按本轮命名前缀查询均为空。

首次重启验收发现 Docker 随机发布的宿主机端口会变化。验证脚本已改为重启后重新读取端口，最终完整验收通过。Compose 的固定 8765 端口无需这一测试处理。

## 5. 复现

```sh
uv sync --python 3.12 --extra dev --extra scout --extra pdf-fixtures
uv run pytest -q
uv run ruff check src scripts tests
uv run python scripts/build_web.py
uv run python scripts/check_assessments.py --data-dir /absolute/new-validation-data
```

本机实际 Docker 目标：

```sh
docker build --network none -t agent-inspect-control:validation examples/assessment
docker image inspect agent-inspect-control:validation --format '{{.Id}}'
# 把实际镜像 ID 作为 --docker-image 参数，使用另一个新 data-dir
uv run python scripts/check_assessments.py \
  --data-dir /absolute/new-docker-validation-data --docker-image sha256:ACTUAL_TARGET_ID
```

完整容器服务：

```sh
docker build -t agent-inspect-judge:validation .
docker image inspect agent-inspect-judge:validation --format '{{.Id}}'
uv run python scripts/check_service_deployment.py \
  --judge-image sha256:ACTUAL_JUDGE_ID --target-image sha256:ACTUAL_TARGET_ID \
  --output /absolute/service-deployment.json
```

首次构建需获取基础镜像与依赖。验证脚本不自动下载目标镜像。输入使用管理员已安装的固定 ID；本轮观测耗时只描述本机控制程序，不能预测真实 Agent 性能。

## 6. 当前范围

尚未完成：真实第三方 Agent 接入、完整 A2A、任意仓库自动构建、源码到镜像的可信来源绑定、官方基准评分、通用工具故障注入、分布式队列、租户隔离和统计置信区间。Token 上限是目标请求约束，用量由目标自报，不能强制供应商侧费用。

当前可交付的是可部署、可实际发题并保存证据的第一版评审服务。正式比赛需要冻结任务标准，适配实际参赛 Agent，并另行验证部署来源与目标的真实输出。
