# 已知源码的 Agent 评测

源码规划会只读扫描固定提交，提取能力线索和 Python 工具候选，再生成受控任务、独立验收规则和漏测清单。出题不调用模型，也不执行仓库代码。实际评测通过已登记的 `target-v1` 服务进行，可能消耗被测 Agent 的模型额度。

## 网页使用

1. 在管理员目标登记表中同时填写 `endpoint` 和 `repository`；`ref` 建议填写固定提交。`repository` 是评审服务能访问的源码目录。使用 Docker 评审服务时需将源码只读挂载到容器。
2. 打开“主动评测”，选择该目标，在“测试模板”中选择“根据源码规划评测”。
3. 设置案例数量、种子、重复次数和案例并发，点击“生成测试文件”。页面会重新扫描源码，展示推荐维度、工具候选及待补测项。
4. 查看题目及适用范围，下载 JSON 或直接开始评测。
5. 在结果中查看“维度覆盖与质量”“重复稳定性”“源码覆盖与漏测”，通过案例链接查看调用轨迹及验收证据。

目标登记示例（路径替换为实际源码路径；不要把 API 密钥填入此文件）：

```json
[
  {
    "id": "my-agent",
    "endpoint": "http://127.0.0.1:9091",
    "health_path": "/health",
    "repository": "/absolute/path/to/agent",
    "ref": "HEAD",
    "demo": false
  }
]
```

如果从 GitHub 自动部署，展开“配置构建与题集”，选择“根据源码规划受控测试”。仍需仓库部署清单或现有 smolagents 接入方式。自定义上传题集优先；源码规划不提供任意仓库的零配置部署。

## CLI 与 API

只读本地仓库并生成题集，完整计划输出到终端，已有题集文件不会被覆盖：

```sh
uv run agent-review plan-repository /absolute/path/to/agent \
  --cases 12 --seed 42 --attempts 3 --concurrency 2 \
  --output suite.repository.json

uv run --env-file .env agent-review assess my-agent \
  --targets targets.json --suite suite.repository.json --data-dir ./.agent-review
```

有部署清单的公开仓库也可直接运行：

```sh
uv run --env-file .env agent-review assess-repo https://github.com/OWNER/REPO \
  --source-plan --cases 12 --attempts 2 --concurrency 2 \
  --data-dir ./.agent-review
```

实际地址和运行期环境变量映射按[仓库部署指南](REPOSITORY_ASSESSMENT.zh-CN.md)配置。smolagents 的脚本离线模型仅支持原模板，不能代表通用源码规划任务的模型能力。

API 先调用 `POST /api/targets/{target_id}/repository-profile` 获取档案 ID，再调用：

```http
POST /api/assessment-suites/plan/{repository_id}
X-Review-Request: 1
Content-Type: application/json

{"cases":12,"seed":42,"attempts":2,"concurrency":2}
```

返回 `suite`、`dimensions`、`tool_candidates`、`gaps` 和请求数量估计。将返回的 `suite` 作为 `POST /api/assessments` 的题集即可。开启服务令牌时还需携带 Bearer 认证。仓库部署 API 可使用 `planning` 对象传同样设置，不能同时传自定义 `suite`。完整仓库导出包含 `assessment_plan`，可以核对计划与冻结题集是否一致。

## 测试维度与答案来源

|维度|测试内容|验收方式|
|---|---|---|
|结构化输出|筛选记录、保持顺序、汇总金额|逐字段严格检查列表和数值|
|指令遵循|去重并按指定方向排序|与程序计算的列表比较|
|上下文检索与引用|从带随机标识的文档找答案|同时检查答案和文档 ID|
|信息不足|查询材料中不存在的信息|检查 `null` 和空引用|
|输入干扰|文档包含伪造的覆盖指令|检查仍返回原任务答案和有效引用|
|组合计算|多步骤算术|程序独立计算答案|
|跨轮记忆|保存随机值后，只发送当前轮请求读取|检查保存响应与读取值|
|会话隔离|写入记忆之后开启新会话|检查新会话返回 NONE|
|工具调用|源码发现名为 calculator 的工具时要求使用|同时检查数值答案与工具轨迹|

基础维度始终进入推荐清单。检索/联网线索增加检索题，记忆线索增加记忆和隔离题，calculator 候选增加工具题；案例数不足时，未选维度显示在漏测清单。每轮覆盖推荐维度后，再生成不同随机数据的案例。

规则逐字段检查业务输出，允许适配器额外附加 `_execution` 等元数据。答案和规则始终留在评审端，不随任务请求发给目标。源码引用解释选题依据，不证明失败原因。通用探测不自动把 README 的完整能力声明标记为已验证。

## 效率、质量与边界

- `concurrency` 为 1–4，默认 1；独立案例可并发执行。多轮、记忆和会话隔离案例作为串行屏障，保留题集顺序。取消后停止派发新任务，已开始的调用在原期限内结束并保存证据。
- 源码规划支持 1–30 个案例、1–3 次重复，每案例默认总期限 10 秒、请求输出预算 2048 Token；最多 300 个请求、900 秒累计任务期限。大型真实任务可编辑期限，但仍须满足总量约束。计划中的请求估计包括重复次数。
- 自动部署复用刚扫描的固定 checkout 档案，避免同一流程重复扫描。源码计划绑定 `repository_source_hash`；扫描内容改变会拒绝直接运行旧计划。Git 仓库只读取提交内容，不读取未提交修改。
- 若做跨版本回归，应建立人工审核的固定基准题集：明确移除源码 hash 绑定，并检查每个版本的源码引用/声明关联。重新出题会改变题集 hash，不能当作相同条件的版本比较。
- 维度报告包含通过、失败、未知、完成数量、耗时 p50/p95 和执行异常；未知保留在分母中。通过率范围表示当前固定题集未知项的最好/最坏情况，**不是统计置信区间**。
- 稳定性在同一案例和预算内统计：重复通过、重复失败、结果波动、证据不全；一次运行不能判定稳定性。
- 缺少 Token、费用或内部轨迹时保留未知。工具规则不会凭正确答案推断调用；自报完整轨迹没有对应工具会失败，不完整/未采集轨迹则保持未知。
- 静态工具候选当前支持 Python `@tool`、直接继承 `Tool`/`BaseTool` 且定义 `forward`/`_run` 的类。间接注册、动态创建、其他语言可能漏检；工具候选不证明运行期注册。
- 除 calculator 的受控数值探测外，其余工具列为待补测。需要领域数据、预期副作用或外部系统的功能，应提供独立题集、可信验收器和测试环境。向量库质量、网页实时事实、代码修复、文档转换及多 Agent 协作不因通用题通过而获认证。
- 并发会影响目标限流和延迟，比较时需保持配置一致。报告不提供未经标定的综合能力排名；合成控制结果也不是参赛 Agent 的真实成绩。

环境变量配置见根目录 [`.env.example`](../.env.example) 和 [README](../README.md)。本地 `.env` 保持忽略，不放入题集或报告。
