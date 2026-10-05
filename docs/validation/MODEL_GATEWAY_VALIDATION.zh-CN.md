# 模型网关最小闭环验收

日期：2026-10-05。用户要求继续受控模型网关阶段。本轮实现为项目内 Python 自动适配试点，使用现有 SQLite 和响应解析，不包含 LiteLLM 部署、独立网关服务或容器出站限制。

## 结果

| 检查 | 结果 |
| --- | --- |
| 全量后端 `uv run pytest -q` | 475 passed、2 skipped；两个已有依赖弃用警告 |
| Ruff | 通过 |
| TypeScript、Vite、内置网页构建 | 通过 |
| Chrome：用量、仓库入口、网关中断恢复 | 9 passed |
| 实际 Docker：网关 + SSE + 正常目标 | 原入口被观察，正式单题 pass，15 Token 只计一次，清理 completed |
| 实际 Docker：模型调用后目标 `os._exit(23)` | 答案 inconclusive、执行 error，保留 ≥15 Token，清理 completed |
| 实际网关子进程：SSE 已返回 usage 后强制 kill | 重启后保留 15 Token 下界、未完成调用为 interrupted，旧凭据全部撤销 |

新增 `tests/test_model_gateway.py` 的 13 项验证覆盖：凭据拒绝/过期/撤销、禁止其他模型/任意上游、预算扣减、相同供应商响应 ID 的实际请求分别计数、同步 JSON、SSE 末尾/缺失结束标记/读取错误/下游断开、失败目标的报告和导出、网关单一统计来源、工具父调用关联、并发轮次隔离、异常供应商用量、实际重试，以及存储和运行中网关的强制退出恢复。

## 实际容器证据

正常流程：

```sh
uv run python scripts/check_llm_adaptation.py --stream --gateway \
  --data-dir /private/tmp/agent-inspect-gateway-docker-20261005
```

- 仓库任务：`repository_job_3f3162d9011443199c6a66cd7b9979dc`。
- 1 次合成适配生成、1 次独立接入检查、1 次正式目标调用。
- 正式模型调用在目标与网关各有原始记录，通用轨迹中只保留 1 条模型事件；输出 Token 5、总 Token 15，缓存命中 6、推理 2 不重复加入总量。
- 模型用量来源为 `gateway`，与生成及接入检查分开。
- 材料：`/private/tmp/agent-inspect-gateway-docker-20261005/validation.bundle.json`。

目标崩溃流程：

```sh
uv run python scripts/check_llm_adaptation.py --stream --gateway --target-crash \
  --data-dir /private/tmp/agent-inspect-gateway-crash-20261005
```

- 仓库任务：`repository_job_f44381981e6346698b40706f78e1f8be`。
- 接入检查成功；正式目标在收到模型响应后直接退出，未返回目标 trace 或答案。
- 案例执行 error、答案 inconclusive，网关持久化的 ≥15 Token 保留。没有将缺失答案判为通过，也没有宣称正式调用返回了入口观察证据。
- 编排任务 completed 表示流程和清理结束，不代表答案验收通过。
- 材料：`/private/tmp/agent-inspect-gateway-crash-20261005/validation.bundle.json`。

两条实际容器流程均使用本地合成仓库和供应商，不代表陌生真实仓库适配率或供应商模型能力。

## 页面、恢复和分发

用独立测试服务重新打开上述崩溃数据目录，端口 18766。`web/tests/gateway.spec.ts` 验证重启后页面显示“已持久化 1 次请求 / 已保存 Token 15”，案例表保留“≥15”，证据包保留来源及逐次网关事件，并通过移动宽度检查。另有 8 项原用量/仓库页面回归通过。

网页、wheel 与项目内 Skill 已刷新，`check_package.py` 通过，Skill 启动器 6 项测试通过。临时网页测试服务已关闭。分发检查包含从 wheel 导入网关存储并读取空汇总，以及现有 API、模板和模型采集兼容检查。

本轮未读取/修改本地 `.env`，保留此前 `.env.example` 改动；没有付费供应商请求。构建和测试材料均在独立临时目录。

## 限制与下一步

只有经过网关且已经持久化的记录可恢复；供应商未返回 usage 的消耗仍未知。目标断开后的读取有期限和 3 秒收尾限制，不保证供应商立即停止计费。网关与评审服务当前同进程，评审服务被强制终止后不会继续接收上游数据，只能恢复已有记录。

首版仅支持 Python 自动适配，默认关闭。同一轮的模型请求串行，不同案例可并发。容器出站网络未限制，尚不能认证全调用覆盖、账单或恶意目标的任务归属。下一步优先建立固定真实 Python 仓库回归集，之后扩展独立网关服务、出站控制、smolagents/清单和跨进程采集。
