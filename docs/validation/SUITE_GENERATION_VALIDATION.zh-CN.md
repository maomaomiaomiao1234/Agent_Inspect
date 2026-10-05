# 自动生成测试文件：验证记录

日期：2026-10-04。本轮实现可复现的 smolagents 题集生成器，不调用模型生成答案，不根据任意仓库猜测接口。

## 已实现

- 网页设置案例数和种子，生成后预览、下载 JSON，或直接使用当前生成题集开始评测。
- CLI：`agent-review generate-suite --template smolagents --cases 12 --seed 42 --output suite.generated.json`；拒绝覆盖已有文件。
- API：`POST /api/assessment-suites/generate`；沿用服务令牌与写请求头，只生成数据，不排队或调用被测目标。
- 1–30 个案例，轮换生成加法、减法、乘法、两步计算、记忆、隔离及公开输入干扰。数值与记忆代码随种子变化，标准答案和 Profile 由程序独立生成。
- 固定生成器版本 `smolagents-v1`，Suite 的描述记录种子/案例数；提交后使用既有题集哈希冻结机制。

## 实际验证

| 检查 | 结果 | 覆盖 |
| --- | --- | --- |
| 后端全量 pytest | 279 passed / 2 已有依赖警告 | 新增 13 项；确定性、边界、认证、无自动提交、文件保留、错误答案拒绝和答案不转发 |
| Ruff | 通过 | `src scripts tests` |
| TypeScript / Vite | 通过 | 新增网页生成、预览、下载及直接使用题集入口 |
| Chrome Playwright | 2 passed | 原有主动评测；生成 7 题并下载/解析 JSON，无自动评测请求，开始按钮可用，移动宽度检查 |
| 真实框架与 HTTP 校准 | 30/30 pass | 最大案例数、最大 seed；实际 smolagents offline 服务，共 34 轮请求 |
| wheel 独立检查 | 通过 | 源码目录外加载更新包，生成 API、schema、网页及既有模块检查 |
| 项目 Skill 分发 | 已更新 | 内置 wheel、runtime 哈希及 `.skill.zip` 已刷新 |

实际 30 题校准 ID：`assessment_4074be29f4dd4e5da3ba8b330be9e82a`；`demo=true`，脚本模型只校准框架接入，不能视为真实模型成绩。题集哈希为 `b404ceb1e5329207ccca4d7fdb7d0c18e212499e764d8f63f43acc2626b4a846`，执行器哈希为 `12b6f9e7e29a2da3f2bedaa4c43ac0bfde6d68f6387091e98bad548726c13691`。

生成器源码 SHA-256：`d92a89a1a562b6e495dc9602dfe6a31d61d03e8483fa7412d253a1d50b507271`。

- [自动生成的默认 12 题 JSON](../../examples/smolagents/suite.generated.json)，文件 SHA-256：`46a2b8b7982f227492af1e54a6e9afab174c12f778a607c4b1a63d7c9c348d46`。
- [实际 30 题校准报告](../reference-assets/examples/smolagents/verified/generated.offline.md)，SHA-256：`dacfab6e0e1c1ed5da0e049b495fec92b364a6be6cbbf2f6fd700fe442aea145`。
- 本机原始结果、上限题集和数据库在 `tmp/smolagents-generated-20261004`，不依赖该临时目录使用生成器。

临时测试服务 18765/9092 已停止；用户的 DeepSeek 目标 9091 保持运行，评审服务在确认无活动任务后重启到更新代码，继续使用原 `review-data-smolagents`。之前两个真实 DeepSeek 任务仍保留。本轮没有新增真实 DeepSeek 调用。

## 当前范围

模板要求示例的固定工具和 JSON 输出约定；它不会自动识别任意 Agent 的接口，也不生成官方基准成绩。标准答案不发送给目标，HTTP 证据保持 partial，内部工具记录仍为目标自报。相同 seed 和案例数可复现题目；改变 seed 后不能直接比较为同题回归。
