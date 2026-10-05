# 固定数字 PDF 转换样例

`source.pdf` 是自制两页英文数字 PDF，包含 10 个内容块、4 个标题、5 段正文和 1 张 5×3 矩形表格。页脚与页码排除在验收内容之外。

- `scripts/build_document_fixture.py` 独立编排 PDF，不读取候选或参考答案。
- `reference.json` 是固定独立标注，已逐页查看渲染结果并核对源 PDF 文本和表格；不是从候选或转换器输出生成的标准答案。
- `correct.json` / `correct.md` 是单独编写的模拟候选。错误场景只修改这些候选，不修改参考。
- `profile.json` 声明 7 项必需检查，以及保持 unknown 的可选 OCR/公式检查。

JSON 中的 ID 是此样例约定的标注槽位，需要由适配器对齐，不是任意 PDF 的自动内容对齐算法。Evaluator 固定核对源 PDF SHA-256 和参考快照，拒绝把导入材料里篡改的参考当作标准答案。

正文与单元格按 NFC Unicode 和空白规范化后比较，保留大小写、标点、数值和单位。标题还比较层级；阅读顺序比较块 ID、页码和顺序；完整性检查缺失、多余块及总页数。表格比较形状、表头和每个单元格，不支持合并单元格。

Markdown 仅支持空行分隔的 ATX 标题、普通段落和首尾竖线的矩形表格。检查器分别解析 Markdown 与 JSON，确保同一内容顺序；不执行或渲染其中 HTML。

没有调用转换 Agent、OCR 或模型，没有评估公式或版式保真。这里的精确匹配不是官方 TEDS/CDM、编辑距离指标或通用文档质量分数。

重新生成/检查 PDF（开发用可选依赖）：

```sh
uv sync --extra dev --extra pdf-fixtures
uv run --extra pdf-fixtures python scripts/build_document_fixture.py
uv run --extra pdf-fixtures pytest -q tests/test_document_conversion.py
```

已安装 Scout 的环境同步时同时加 `--extra scout`。生成 PDF 使用 invariant 模式；不同 ReportLab 版本仍可能改变字节，更新固定 PDF 后必须重新视觉检查与审查标注，再显式更新 reference 的 hash/version，不能自动接受新 hash。
