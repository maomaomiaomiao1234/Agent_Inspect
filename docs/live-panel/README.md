# Agent Inspect Live Panel

根 [README](../../README.md#live-panel) 中的动态架构图，按项目的实际模块和处理流程定制。

- [GIF 动画](../images/agent-inspect-live-panel.gif)：README 直接展示，18 秒循环。
- [静态图](../images/agent-inspect-live-panel.png)：适合关闭动画或导出文档时使用。
- [MP4](../images/agent-inspect-live-panel.mp4)：1440 × 1080、20 fps、无音轨。
- [独立 HTML](index.html)：下载后用浏览器打开即可播放，无服务器或网络依赖。GitHub 文件页显示源码，需下载后打开。
- [JSON 配置](config.json)：文字、配色、布局、连线和状态机的唯一内容来源。

## 内容依据

| 画面内容 | 项目依据 |
| --- | --- |
| 主动评测、case × budget × attempt、冻结题集、session 隔离、target-v1 | [主动评测指南](../guides/ACTIVE_ASSESSMENT.zh-CN.md)、`src/agent_trace_review/assessments.py` |
| 公开仓库接入、固定提交、Docker 构建与自动适配 | [仓库评测指南](../guides/REPOSITORY_ASSESSMENT.zh-CN.md)、`src/agent_trace_review/repository_jobs.py` |
| OpenCode / 通用轨迹导入，任务、diff 与验证材料 | [项目任务范围](../../README.md#项目处理哪些任务)、`src/agent_trace_review/opencode.py`、`src/agent_trace_review/generic.py` |
| 12 类任务、7 个能力维度 | [通用评测指南](../guides/GENERAL_ASSESSMENT.zh-CN.md)、`src/agent_trace_review/general_suite.py` |
| 独立 Profile、自定义评估器、pass / fail / unknown | `src/agent_trace_review/profiles.py`、`src/agent_trace_review/analysis.py` |
| Token 缺失为 unknown | [目标调用记录](../guides/TARGET_TELEMETRY.zh-CN.md)、`src/agent_trace_review/usage_accounting.py` |
| SQLite、内容寻址工件与报告导出 | `src/agent_trace_review/storage.py`、`src/agent_trace_review/assessment_store.py`、`src/agent_trace_review/reports.py` |
| FastAPI 后端、React / TypeScript 前端、v0.3.0 | `pyproject.toml`、`web/package.json` |

**这是一张流程示意图。** 入口高亮、执行状态、三种验收结果、日志、时间和信号计数均由动画状态机生成，不连接评测 API，也不代表实际执行次数、通过率或资源用量。三种入口会轮流高亮，已有轨迹通过虚线直接进入评审；Profile 留在评审端，被测 Agent 只收到公开任务与预算。

## 修改与重新生成

修改 [config.json](config.json)，在仓库根目录执行：

```sh
python3 scripts/build_live_panel.py
```

渲染需要 Python 3.11+、Chrome / Chromium 和 ffmpeg；不需要额外 Python 包。macOS 自动查找 `/Applications/Google Chrome.app`，其他平台从 PATH 查找浏览器，也可显式指定：

```sh
python3 scripts/build_live_panel.py \
  --chrome /path/to/chrome \
  --ffmpeg /path/to/ffmpeg
```

脚本先检查 124 个时间点的文字溢出与重叠，并对 4 个采样帧做重复定位的像素对比；检查通过后，生成 HTML、MP4、12 fps 的 GIF 和静态图。GIF 使用 1200 × 900 尺寸以控制仓库文件体积。字体由本机提供，换渲染环境后应重新检查布局。

## 来源与许可

渲染引擎来自 [ythx-101/live-panel-skill](https://github.com/ythx-101/live-panel-skill)，固定到提交 `8a70aa2c4e3fac68b40e2472407e32e2637a7a36`。所用 `template.html`、`render.py`、`check_frames.py` 和 `livepanel.py` 保存在 `scripts/live_panel/`，附带 [上游 MIT 许可](../../scripts/live_panel/LICENSE)。仅调整了 `livepanel.py` 的模板根路径以适配本项目目录。

运动方式参考该仓库的 [motion grammar](https://github.com/ythx-101/live-panel-skill/blob/8a70aa2c4e3fac68b40e2472407e32e2637a7a36/references/motion-grammar.md)，原始动态面板创意出自 [@thedelost](https://x.com/thedelost/status/2105398038026195279)。本图的布局、文案与数据流根据 Agent Inspect 重新设计。
