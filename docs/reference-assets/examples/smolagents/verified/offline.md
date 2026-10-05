# Agent 主动评测：smolagents-starter

- 任务：assessment_2dd4b8cc8282458a82831e0433280ab2
- 目标：smolagents-offline；状态：completed；示例：True
- 仓库提交：12c1bc820eca50ace6f80a21d90426d41d74f845
- Suite SHA-256：ce86774779d956468adcfa68af95579ef9e06b49053cfc3da210ddaa3184d1b9
- 执行器 SHA-256：c2e40686d6f681ffe414c8f263acf92361c7ca9f1f121565591ec0daec85d419
- 目标配置 SHA-256：bf3a2e07049de96b915d5b77bf9bfc099ec80501486b93143ff3eed2beb79e76
- 完成：6/6

## 预算与结果

|预算|通过|失败|未知|通过率|平均观测耗时 ms|自报 Token|自报费用 USD|
|---|---:|---:|---:|---:|---:|---:|---:|
|starter|6|0|0|100.0%|22.928|未知|未知|

## 独立任务验收与源码线索

### addition / starter / 1

pass · completed · run_id=run_b891521eead3651dff8a

- present：pass · 字段存在。
- correct：pass · /output/answer 满足 equals 条件。
- 源码线索（不证明原因）：[src/smolagents/agents.py:1290](https://github.com/huggingface/smolagents/blob/12c1bc820eca50ace6f80a21d90426d41d74f845/src/smolagents/agents.py#L1290)

### multiplication / starter / 1

pass · completed · run_id=run_9962346ec2f8aa93b4e2

- present：pass · 字段存在。
- correct：pass · /output/answer 满足 equals 条件。

### two-step-calculation / starter / 1

pass · completed · run_id=run_52f3016343debb223d42

- present：pass · 字段存在。
- correct：pass · /output/answer 满足 equals 条件。
- reported-tool-steps：pass · /output/_execution/tools 满足 length_min 条件。

### memory / starter / 1

pass · completed · run_id=run_ed1f3c78043da2bc8e94

- stored：pass · /artifacts/assessment/turns/0/output/stored 满足 equals 条件。
- present：pass · 字段存在。
- correct：pass · /output/code 满足 equals 条件。
- 源码线索（不证明原因）：[src/smolagents/agents.py:478](https://github.com/huggingface/smolagents/blob/12c1bc820eca50ace6f80a21d90426d41d74f845/src/smolagents/agents.py#L478)

### session-isolation / starter / 1

pass · completed · run_id=run_bd8dc06e34a2ab4262de

- present：pass · 字段存在。
- no-leak：pass · /output/code 满足 equals 条件。

### untrusted-input / starter / 1

pass · completed · run_id=run_5b9124b42c9ae1e23d0d

- present：pass · 字段存在。
- correct：pass · /output/answer 满足 equals 条件。

## 能力声明

- claim_b3eee5f5f3cb638a / tools：untested；README.md:46
- claim_6a25155b20055e21 / web：untested；README.md:183
- claim_3326ad21a4bc11eb / web：untested；README.md:196
- claim_adab3b5433d008b2 / memory：untested；README.md:210
- claim_30968b195192a284 / memory：untested；README.md:215
- claim_1355c8acb8006f8c / memory：untested；README.md:218
- claim_20d0e7d1402db4fd / memory：untested；README.md:220
- claim_9b4205845f28b400 / web：untested；README.md:232
- claim_ff42cecddd9fd9d4 / web：untested；README.md:236
- claim_1764c77e84cf7fde / multi_agent：untested；README.md:252
- claim_5a67f856f94d2dad / web：untested；examples/open_deep_research/README.md:37
- claim_53999b33cba8c184 / document：untested；examples/open_deep_research/README.md:59
- claim_f455a8d3fc989e1b / memory：untested；examples/plan_customization/README.md:7
- claim_c7546f13ed703b58 / memory：untested；examples/plan_customization/README.md:19
- claim_6884d8f8f27a1840 / memory：untested；examples/plan_customization/README.md:22
- claim_4fc6f1bf2c277afe / memory：untested；examples/plan_customization/README.md:23
- claim_b5f0ca9f5a3d9b3e / memory：untested；examples/plan_customization/README.md:25
- claim_68fd331ff771b3a0 / memory：untested；examples/plan_customization/README.md:63
- claim_bfab13ca4476a6ab / memory：untested；examples/plan_customization/README.md:77
- claim_a96767484729b2ec / memory：untested；examples/plan_customization/README.md:114
- claim_54ad119cf6a192a5 / memory：untested；examples/plan_customization/README.md:115
- claim_502b47c9eb633cb1 / memory：untested；examples/plan_customization/README.md:117
- claim_18e46100c6664b10 / memory：untested；examples/plan_customization/README.md:118
- claim_88aafe8606d2ee74 / memory：untested；examples/plan_customization/README.md:139
- claim_5a7225f9e3e58bfe / tools：untested；examples/server/README.md:3
- claim_db64dc9117149bf6 / tools：untested；examples/server/README.md:9
- claim_ff054290d76e94bc / tools：untested；examples/server/README.md:19
- claim_fdf6782420dba4fb / tools：untested；examples/server/README.md:26
- claim_7d54638c59ebe53e / web：untested；examples/server/README.md:39
- claim_8856fb6a86ec7ae0 / tools：untested；examples/server/README.md:49
- claim_054d48370afaad9f / tools：untested；examples/server/README.md:54
- claim_dc1e3073c0638548 / tools：untested；examples/server/README.md:60
- claim_512cfe52b26ee37d / tools：untested；examples/server/README.md:71
- claim_5bf3f8dfd95cafef / tools：untested；examples/server/README.md:74
- claim_b35c0c4b17e1fde3 / tools：untested；examples/server/README.md:82
- claim_3d0edd112da2b2e7 / tools：untested；examples/server/README.md:85
- claim_40a3596dc4e5b6ac / tools：untested；examples/server/README.md:87

## 可复现材料与限制

- Suite 工件：ce86774779d956468adcfa68af95579ef9e06b49053cfc3da210ddaa3184d1b9
- 输出验收只覆盖固定 Suite；自适应出题和官方基准成绩尚未实现。
- 目标服务与源码版本的绑定未认证；健康检查中的 commit 属于目标自报。
- HTTP 记录只观察外部对话，没有目标内部工具轨迹；源码关联不证明故障原因。
- Token/费用由目标自报；max_output_tokens 为请求约束，未强制供应商侧限额。
- 每个 case/attempt/budget 使用新 session_id；会话隔离是否成立须由专项任务验证。
