# 模型与工具遥测验收

日期：2026-10-04。实现基于 GitHub main `d51fb151a57356ff857a127ae37b5719a2f1e473`，测试使用独立数据目录。目标自报调用及覆盖范围不等同于独立认证。

## 范围

- target-v1 增加可选逐次 trace，以及保留失败轨迹的 execution_status/error。
- smolagents 真实模型后端和工具入口记录状态、耗时、用量和工具参数/结果；包含 final_answer 与工具校验失败。
- 多轮绑定、调用 id 去冲突、汇总与逐次 Token 去重、缺失用量保留未知；父调用与 session/case/job/run 可关联。
- 标准工具规则、资源 Profile、重复失败及恢复诊断；网页、Markdown/JSON/证据包导出。
- 新采集器进入示例 Dockerfile、仓库自动构建上下文及 Python wheel。

## 自动化检查

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 后端回归 | 326 passed / 2 skipped / 2 warnings | 未安装可选 PDF/Scout 依赖，相关检查跳过；两个既有依赖弃用警告 |
| 新协议、统计及导出 | 18 passed | 完整/部分/旧目标、失败保存、工具恢复、Token 去重、会话/父调用/用量异常、凭据脱敏 |
| 构建配方与新协议专项 | 49 passed | 验证 telemetry.py 随源码构建并参与内容哈希 |
| smolagents 独立适配 | 11 passed | 实际框架和本机模型 SDK fixture，额外覆盖工具失败恢复与模型错误 |
| Chrome 专项 | 3 passed | 原主动评测、题集生成及新的实际 offline 遥测；包含手机布局 |
| Ruff、TypeScript/Vite | passed | 主代码、脚本、测试与采集器 |
| 源码外 wheel 检查 | passed | API、UI、schema、打包适配器与采集器 |

受限制的测试执行环境曾拒绝绑定本机端口和查询测试子进程；在允许本机测试的执行环境重跑后通过，未关闭这些测试。

## 真实框架 offline 校准

通过本机 HTTP 服务执行生成器 seed=81 的 12 个案例，实际使用 smolagents 1.26.0，脚本规划器，无模型推理和费用。

- assessment：`assessment_d6940fe3742b4f2387736520ebd147e1`
- 12/12 pass，demo=true。
- 30 次规划调用、30 次工具入口；工具次数包含 final_answer。
- 每个案例内部记录声明 complete；Token、费用仍为 unknown。
- 覆盖算术、链式计算、多轮记忆、隔离和公开干扰输入。

## 最小真实 DeepSeek 验证

实际根目录 `.env` 的两组模型配置均为官方 DeepSeek 地址、`deepseek-flash`，密钥已填写且模型列表认证 HTTP 200。密钥不写入该记录。

- assessment：`assessment_b31c386966064f989f1db5551d4d92b7`
- run：`run_ac1964125c8c3c080528`
- 任务：`Compute 12 + 7`；独立答案检查 1/1 pass，demo=false。
- 2 次实际模型请求；工具为 calculator、final_answer。
- 输入 Token 3166、输出 99、总计 3265；逐次模型用量之和与本轮汇总一致，未相加两次。
- 自报模型耗时合计 1398.995667 ms，工具耗时合计 0.224416 ms。
- 内部记录由目标声明 complete；未返回费用，保持 unknown。
- Markdown、评测 JSON、证据包均检查没有配置密钥的原文。

此处只核验一题的真实协议和采集链路，不证明一般能力、官方基准成绩或统计显著性。完整范围、协议约束及接入方法见 [调用记录指南](../guides/TARGET_TELEMETRY.zh-CN.md)。
