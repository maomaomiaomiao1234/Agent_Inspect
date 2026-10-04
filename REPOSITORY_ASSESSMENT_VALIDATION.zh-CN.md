# 仓库自动部署评测验收（2026-10-04）

本轮实现并验证公开 GitHub 仓库地址 → 固定源码 → Docker 构建 → 健康检查 → 独立评测 → 导出证据 → 清理的完整流程。使用方法见 [仓库评测指南](docs/REPOSITORY_ASSESSMENT.zh-CN.md)。

## 真实 GitHub 与 Docker 验证

最终命令只指定仓库地址、数据目录和输出路径，没有手工 clone、登记目标、启动被测服务或指定版本/题集：

```sh
uv run agent-review assess-repo https://github.com/huggingface/smolagents \
  --data-dir ./review-data-repository --output ./repository-result.json
```

最终运行从 HEAD 解析并检出 `c30b115286e000e98711fae5e85993547b73d826`，安装检出源码的 smolagents `1.27.0.dev0`，构建镜像 `sha256:627c7d64c23e4ff580f6f629f585a87c3d7e20a125dfac58693a871c88683000`。健康响应的版本与提交和构建记录一致；评审端生成 seed=42 的 12 题，结果 **12 pass / 0 fail / 0 unknown**。

- 仓库任务：`repository_job_0b0d5bd9e04f44de9415586f17fbacbd`
- 评测任务：`assessment_2b499feec84b4a7cab0b22933a513b63`
- 完整证据：[repository.bundle.json](examples/repository_assessment/verified/repository.bundle.json)，包含源码档案、上下文/适配器哈希、构建日志、镜像、题集、请求响应与逐题判定。
- 完成状态：`completed`；清理状态：`completed`。工作目录已删除，运行容器及任务镜像标签已回收；Docker 的共享构建缓存由管理员管理。

这是**真实框架、真实拉取和真实 Docker 部署的 offline 校准**，使用脚本模型，`demo=true`，没有调用付费模型，不是模型能力排名。Token 与费用未报告，保持 unknown/null。固定提交和构建输入哈希可追溯，但依赖版本由仓库/Dockerfile 决定，不承诺位级可复现或上游签名认证。

## 异常恢复和原审核问题

`scripts/check_repository_recovery.py` 实际启动固定 Docker 控制镜像，待健康后让评测子进程以 `os._exit(17)` 退出。此时容器仍在运行；新 AssessmentManager 读取持久化资源记录后删除遗留容器，并将评测标为 `interrupted`。记录见 [recovery.json](examples/repository_assessment/verified/recovery.json)。只有属于当前数据目录 owner 的容器会被清理。

此次也修复原审核中的 JSON 历史 422、已知 Token 未扣除/超额误判，以及容器身份未持久化问题，均有回归测试。进程看守机制覆盖主进程崩溃后 Git/构建客户端的终止；短命令正常结束亦有回归测试。重启只标记中断并回收，不自动重跑题目。

## 自动化验证

| 检查 | 结果与范围 |
| --- | --- |
| 主项目 pytest | 310 passed / 2 dependency deprecation warnings |
| 独立 smolagents pytest | 9 passed；真实框架工具调用、JSON 历史、隔离、认证、版本和本地 SDK HTTP 模拟 |
| Ruff | src、scripts、tests、smolagents 适配器及其测试通过 |
| TypeScript / Vite | 构建通过 |
| Playwright | 主动评测及仓库页面 4 passed；调整 URL 输入宽度后，仓库桌面/移动流程 2 passed 再验证 |
| wheel / skill | wheel 脱离源码目录加载成功；网页、契约、生成题集与内置适配器检查通过；skill 内置 wheel 与内容哈希已更新 |

仓库任务测试覆盖 URL/路径/环境变量约束、构建上下文排除敏感文件、答案由评审端提供、队列取消、超时/日志上限、进程看守、持久化回收、服务互斥、API 开关与认证、实际本地 Git 配合模拟 Docker 编排等。Playwright 的仓库流程使用 API fixture 验证交互与下载；真实远端构建另由上述 CLI 验证，不能将两者混称为浏览器端到端真实构建。

普通测试沙箱禁止本地端口监听和 `ps`，相应集成测试在允许本机端口/子进程检查的环境执行。未改动用户原有评测数据库或正在运行的目标服务。

## 支持范围

仅提供 URL 自动适配当前内置的 smolagents；其他仓库需 `agent-review.json`、符合 target-v1 的服务和独立题集（或明确选择兼容的模板）。目前仅支持公开 GitHub HTTPS URL。API 构建默认关闭，需管理员显式启用；运行时凭据按允许的环境变量名映射，不进入构建上下文。

尚未覆盖私有仓库、SSH/LFS/子模块、任意 README 自动安装、官方基准、远端集群部署或恶意多租户构建的强隔离。构建依赖下载需要网络，构建磁盘与宿主机配额应由专用 Docker 环境管理。
