# 主动评测控制 Agent

这里包含真实运行的 HTTP 控制程序和冻结题集，用于校准评审器。程序没有调用模型，也不是能力基准。

```sh
uv run python examples/assessment/mock_agent.py --port 9081
# 另一终端
uv run agent-review serve --data-dir ./review-data \
  --targets examples/assessment/targets.json --port 8765
```

网页打开 `/#/assessments`，上传 `suite.json`。题集 4 个案例、2 档预算、每项重复 2 次，共 16 项结果。

| 服务路径 | 预期 | 行为 |
| --- | --- | --- |
| /correct | 16 pass | 按固定控制逻辑响应，记忆按 session 保存 |
| /incorrect | 16 fail | 返回可解析的错误答案 |
| /noop | 16 fail | 返回空对象 |
| /missing | 16 inconclusive | 自报完成，缺少合法输出协议 |
| /leaky | 12 pass / 4 fail | 用共享全局记忆，隔离案例失败 |
| /timeout | 取决于期限 | 延迟 2 秒，用于超时测试，未在默认登记表启用 |

memory 案例第二轮仅提供当前消息，目标需保留同一 session 的状态。其他案例使用新的 session；隔离案例应返回 NONE。示例未提供用量，不能填成零。

`Dockerfile` 仅打包这个控制程序，实际执行由 Python 标准库 HTTP 服务完成。用于检查固定镜像启动和清理：

```sh
docker build --network none -t agent-inspect-control:local examples/assessment
docker image inspect agent-inspect-control:local --format '{{.Id}}'
uv run python scripts/check_assessments.py \
  --data-dir /absolute/new-test-data --docker-image sha256:ACTUAL_IMAGE_ID
```

完整接入说明见 [主动评测指南](../../docs/ACTIVE_ASSESSMENT.zh-CN.md)。`verified/` 中的记录为历史离线证据；读取它们不会启动 Agent 或 Docker，不能作为当前版本的在线结果。
