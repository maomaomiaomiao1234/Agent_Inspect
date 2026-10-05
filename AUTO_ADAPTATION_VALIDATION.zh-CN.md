# Python LLM 自动适配验收

日期：2026-10-05。实现授权来自用户「开始实现」。本轮未读取、修改或上传本地 `.env`，未新增付费供应商调用。

## 验证结果

|检查|结果|
|---|---|
|后端完整回归|406 passed / 2 skipped / 2 warnings|
|自动适配专项|18 passed，含生成输出、截断用量、取消、凭据处理、原入口、会话、同步/异步采集和失败保存|
|仓库管线专项|5 种情况通过：成功、构建失败修复、未观察入口修复、不支持、取消|
|Chrome|10 passed，含未知仓库参数、修复次数、分开 Token、下载及移动宽度检查|
|实际 Docker|生成接入文件构建成功，target-v1 健康/任务、原入口、模型与 calculator 调用、评测和清理成功|
|静态与交付|Ruff、git diff --check、TypeScript/Vite、wheel 与 Skill 同步|

现有两项跳过为可选 Scout/PDF 依赖检查；弃用警告来自 Starlette/httpx/AnyIO。浏览器的仓库 API 使用控制响应，Token 测试使用本机合成目标。

## 实际 Docker 材料

执行 `.venv/bin/python scripts/check_llm_adaptation.py`。脚本使用真实 Docker、当前生成/构建/评测管线、本地合成生成与模型 HTTP API，以及本地合成 Python 源码。Git 固定提交来自本地 fixture，未重新验证公网仓库拉取。

- 仓库任务：`repository_job_d47aa7ecf01d4b1e99c49babd0a93d34`。
- 状态：completed；资源清理：completed；正式单案例：pass。
- 原入口：固定源码 `NativeAgent.run`，观察到调用；正式任务同时采集 calculator。
- HTTP 次数：生成 1、目标 2（独立接入检查 1 + 正式 1）。
- 正式模型用量：输入 ≥10、输出 ≥5、总 ≥15，来源 calls；覆盖 partial，不推断费用。
- 证据包：`/var/folders/b1/8svk3j492db7gxr86z4hscpw0000gn/T/agent-inspect-auto-adapt-validation-rlt2r19d/validation.bundle.json`，临时目录不随仓库交付。
- 检查包内未包含合成密钥，任务结束清理自己的容器、运行环境文件、源码上下文和镜像标签。

该次验收发生在增加网页用量摘要和 ZIP 下载前；对应摘要、ZIP 和修复导出由后续管线测试验证。模型 API 为合成供应商，这证明实际运行链路可用，不证明真实 LLM 生成质量或任何陌生仓库必然能自动接入。

## 关键回归

- 固定 native 源码不改写；生成代码静态检查，不在宿主机执行。
- 无效符号/定义行、未提供源文件、非法依赖/路径、额外未许可环境变量拒绝。
- 独立模型调用导入被拒绝；实际任务未观察到原入口，能力结果不能通过。
- 构建与接入失败均可有限修复，保留各轮记录和独立验证任务。
- 显式原生模型包装与 httpx 观察不重复计数；输出预算仍传给实际模型请求。
- 异步原生调用和调用后失败保留已返回用量；partial 不伪装完整。
- 同一会话保留原实例，不同会话使用独立实例；缺少凭据拒绝访问目标。
- 生成、接入检查和正式评测用量分别记录，正式案例数与分母不包含接入检查。

实际陌生仓库的 LLM 适配成功率、复杂图/外部服务和全框架工具遥测仍需后续专项试点。使用与范围见 [自动适配指南](docs/AUTO_ADAPTATION.zh-CN.md)。
