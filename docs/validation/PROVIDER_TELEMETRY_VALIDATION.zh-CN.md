# 通用模型用量采集第二阶段验收

日期：2026-10-05。用户确认继续 `docs/TOKEN_USAGE_EXPLORATION.zh-CN.md` 第二阶段。本轮实现与验证均使用独立临时数据，不读取或修改本地 `.env`，没有付费供应商调用。进入本轮前已有的 `.env.example` 改动保持原样。

## 实现范围

- 新增独立 `provider_telemetry.py`，提供 Chat Completions、Responses、DeepSeek 和原生 token_usage 规范化；同步/异步包装，以及按任务上下文启用的 httpx JSON/SSE 观察。
- 记录每次实际 HTTP 请求，包括 SDK 自动重试；显式/嵌套包装不重复计数。流式 usage 按快照读取，不累加重复 chunk。零与未知区分，错误/取消/提前关闭保留已知下界。
- 保留响应模型、请求模型、供应商请求 ID、响应 ID，以及评测/案例/预算/尝试/会话/轮次/调用关联。上下文随 asyncio 和 `to_thread` 传播；原生线程和跨进程需显式传播。
- 协议增加可选缓存命中、写入、未命中细分，继续保留 reasoning；细分不加进总量。原五字段摘要继续可读，原 target-v1 请求 JSON 不变。
- 自动适配运行模板复用模块；构建输入包含模块源码和哈希，适配文件 ZIP 包含模块。统计、运行指标、资源规则、网页及 Markdown/JSON/证据包接入相同用量。

使用见 [通用采集指南](../guides/PROVIDER_TELEMETRY.zh-CN.md)。

## 自动化验证

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q` | 462 passed，2 skipped | 全量后端；已有两个依赖弃用警告 |
| `uv run ruff check src scripts tests examples/assessment/mock_agent.py` | 通过 | Python 静态检查 |
| `git diff --check` | 通过 | 补丁空白检查 |
| `uv run python scripts/build_web.py` | 通过 | 生成类型、TypeScript、Vite、内置网页 |
| Chrome：`usage.spec.ts repository-assessments.spec.ts` | 8 passed | 完整/部分/离线用量、缓存/推理字段、JSON 下载、运行指标、移动宽度与仓库评测 |
| OpenAI SDK 3.24.0 | 通过 | 同步 Chat、自动重试、Chat 流式、异步 Chat/Responses、Responses 流式；全部使用 MockTransport |

新增 `tests/test_provider_telemetry.py` 的控制用量为每次 10 输入、5 输出、15 总 Token；两次 20/10/30，两轮 40/20/60。还覆盖 gzip、逐字节 CRLF/SSE、CR 换行、缺少 usage、缺少结束标记、显式关闭、读取错误、取消、并发上下文、嵌套包装、重复导入、旧摘要、过大事件后继续采集，以及无摘要的通用导入保留 partial。

SDK 验证命令：

```sh
PYTHONPATH=src examples/smolagents/.venv/bin/python scripts/check_provider_sdk.py
```

同步 3 个请求对应 3 个事件，包含 1 个无 usage 的重试，已知 30 Token 标 partial；异步 3 个成功请求共 45 Token。未安装新的 SDK 依赖。

Chrome 最初因沙箱阻止启动而失败，获准在沙箱外重新运行后 8/8 通过。服务使用 `/private/tmp/agent-inspect-provider-ui-20261005` 和端口 18766；运行期间未加载本地 `.env`，验收后临时服务已关闭。

## 实际 Docker 验证

```sh
uv run python scripts/check_llm_adaptation.py --stream \
  --data-dir /private/tmp/agent-inspect-provider-docker-20261005
```

仓库任务 `repository_job_84d123a1c84e4f598fc4728f3e1424f1`：completed；原入口被观察；正式单题 pass；资源清理 completed。1 次合成生成请求、1 次接入检查、1 次正式目标请求，目标使用 SSE。

正式任务记录 ≥15 Token、缓存命中 6 和推理 2；默认 partial，因为自动模板不宣称覆盖所有内部工具或其他网络入口。上下文的 assessment/case/budget 和供应商请求 ID 验证通过。生成、接入检查及正式用量保持分开。

材料：`/private/tmp/agent-inspect-provider-docker-20261005/validation.bundle.json`。合成目标和合成供应商只验证实际容器、流式解析、任务协议、源码入口观察与清理，不代表陌生真实仓库适配成功率或模型能力成绩。

## 分发与限制

网页、0.3.0 wheel 和项目内 Skill 引擎已刷新；`check_package.py` 通过，Skill 启动器 6 项测试通过。wheel 检查覆盖从包内导入通用采集模块、自动运行模板及现有 API 流程。版本号未变，内容哈希和执行器哈希会变化。

当前是目标进程内采集，不包含受控模型网关。其他网络库、绕过配置端点、未传播上下文的线程/进程及未包装工具可能缺失；目标崩溃或评测 HTTP 超时且没有返回时，评测器不能恢复进程内用量。历史缺失数据不补写，费用不按 Token 估算。下一阶段为受控模型网关试点。
