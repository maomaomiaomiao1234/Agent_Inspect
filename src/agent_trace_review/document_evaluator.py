"""Fixed document fixture evaluator; never trusts candidate-supplied answers.

This is a small aligned-block benchmark, not TEDS/CDM, OCR or a general PDF parser.
Only this module and the packaged reference define the acceptance semantics.
"""

import base64
import binascii
import json
import re
import unicodedata
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .contracts import Contract
from .profiles import PROTOCOL
from .util import canonical, digest

EVALUATOR_VERSION = "document-fixture/1"
RESOURCE = "fixtures/document_conversion"
MAX_PDF_BYTES = 4 * 1024 * 1024


def fixture_json(name):
    return json.loads(files("agent_trace_review").joinpath(RESOURCE, name).read_text())


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


class Block(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    page: int = Field(ge=1, le=1000, strict=True)


class Heading(Block):
    kind: Literal["heading"]
    text: str = Field(max_length=10000)
    level: int = Field(ge=1, le=6, strict=True)


class Paragraph(Block):
    kind: Literal["paragraph"]
    text: str = Field(max_length=10000)


class Table(Block):
    kind: Literal["table"]
    rows: list[list[Annotated[str, Field(max_length=1000)]]] = Field(min_length=2, max_length=500)

    @model_validator(mode="after")
    def rectangular(self):
        if not 1 <= len(self.rows[0]) <= 30 or any(len(row) != len(self.rows[0]) for row in self.rows):
            raise ValueError("表格必须为矩形，包含表头，最多 30 列。")
        return self


class Document(Contract):
    document_id: str = Field(min_length=1, max_length=200)
    page_count: int = Field(ge=1, le=1000, strict=True)
    blocks: list[Annotated[Heading | Paragraph | Table, Field(discriminator="kind")]] = Field(max_length=2000)

    @model_validator(mode="after")
    def unique_blocks(self):
        if len({b.id for b in self.blocks}) != len(self.blocks):
            raise ValueError("内容块 ID 必须唯一。")
        if any(b.page > self.page_count for b in self.blocks):
            raise ValueError("内容块页码超出 page_count。")
        return self


class DocumentOutput(Contract):
    document: Document
    markdown: str | None = Field(None, max_length=100000)


def source_pdf(artifacts):
    """Read only embedded bytes; no filesystem/URL references or external commands."""
    source = artifacts.get("source_document")
    if not isinstance(source, dict) or source.get("kind") != "digital-pdf":
        raise ValueError("缺少已支持的数字 PDF 材料；当前不验收扫描 OCR。")
    encoded = source.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_PDF_BYTES + 2) // 3 * 4:
        raise ValueError("缺少 PDF 内容，或超过 4 MiB。")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("PDF 内容不是有效 base64。") from exc
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        raise ValueError("PDF 内容无效。")
    if digest(content) != source.get("sha256"):
        raise ValueError("PDF 字节与声明的哈希不一致。")
    return content


def signatures(blocks):
    result = []
    for block in blocks:
        item = {"kind": block.kind}
        if isinstance(block, Table):
            item["rows"] = [[normalize(cell) for cell in row] for row in block.rows]
        else:
            item["text"] = normalize(block.text)
            if isinstance(block, Heading):
                item["level"] = block.level
        result.append(item)
    return result


def markdown_signatures(markdown):
    """Restricted Markdown: ATX headings, paragraphs and rectangular pipe tables."""
    blocks = []
    for chunk in re.split(r"\n\s*\n", markdown.replace("\r\n", "\n").strip()):
        lines = [line.strip() for line in chunk.splitlines()]
        if not lines:
            continue
        heading = re.fullmatch(r"(#{1,6})[ \t]+(.+)", lines[0])
        if heading:
            if len(lines) != 1:
                raise ValueError("标题后应有空行。")
            blocks.append({"kind": "heading", "level": len(heading[1]), "text": normalize(heading[2])})
        elif lines[0].startswith("|"):
            if len(lines) < 3 or any(not line.startswith("|") or not line.endswith("|") for line in lines):
                raise ValueError("表格需要首尾竖线、表头、分隔行和数据行。")
            rows = [[normalize(cell) for cell in line[1:-1].split("|")] for line in lines]
            if any(not re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
                raise ValueError("无效表格分隔行。")
            if any(len(row) != len(rows[0]) for row in rows):
                raise ValueError("Markdown 表格列数不一致。")
            blocks.append({"kind": "table", "rows": [rows[0], *rows[2:]]})
        else:
            blocks.append({"kind": "paragraph", "text": normalize(chunk)})
    return blocks


def evaluate_document(request):
    """Produce evaluator-v1 results for the fixed PDF, with independently bound references."""
    context = request.get("context")
    if not isinstance(context, dict) or request.get("protocol") != PROTOCOL:
        raise ValueError("需要有效 evaluator-v1 请求。")
    if request.get("input_hash") != digest(context) or request.get("run_id") != context.get("run_id"):
        raise ValueError("请求内容与 input_hash/run_id 不一致。")
    response = {key: request[key] for key in ("protocol", "run_id", "input_hash", "profile_hash")}
    reference = fixture_json("reference.json")
    artifacts = context.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError("artifacts 必须为对象。")
    binding_error = None
    try:
        content = source_pdf(artifacts)
        if digest(content) != reference["source_sha256"]:
            raise ValueError("PDF 不匹配固定独立参考；需要适用于该文档的评估器。")
        if canonical(artifacts.get("reference")) != canonical(reference):
            raise ValueError("缺少固定独立参考快照，或快照被修改；不会采用候选提供的答案。")
    except ValueError as exc:
        binding_error = str(exc)

    output = context.get("output")
    parsed = None
    output_error = None
    if output is not None:
        try:
            parsed = DocumentOutput.model_validate(output)
        except ValidationError as exc:
            output_error = str(exc.errors(include_input=False))[:1000]
    expected = Document.model_validate({k: reference[k] for k in ("document_id", "page_count", "blocks")})
    results = {}

    def record(key, passed, explanation, paths):
        results[key] = {
            "id": key,
            "status": "pass" if passed else "fail",
            "explanation": explanation,
            "evidence_paths": paths,
        }

    if not binding_error and parsed:
        document = parsed.document
        reference_path = "/artifacts/reference"
        output_path = "/output/document"
        by_id = {b.id: b for b in document.blocks}
        expected_ids = [b.id for b in expected.blocks]
        actual_ids = [b.id for b in document.blocks]
        record(
            "document_source",
            document.document_id == reference["document_id"],
            "PDF 字节匹配固定参考；核对输出 document_id。",
            ["/artifacts/source_document/sha256", reference_path, output_path + "/document_id"],
        )
        missing = sorted(set(expected_ids) - set(actual_ids))
        extra = sorted(set(actual_ids) - set(expected_ids))
        complete = not missing and not extra and document.page_count == expected.page_count
        record(
            "document_completeness",
            complete,
            f"内容块 {len(actual_ids)}/{len(expected_ids)}；页数 {document.page_count}/{expected.page_count}；"
            f"缺失 {missing or '无'}；多余 {extra or '无'}。页脚/页码不计入内容块。",
            [output_path, reference_path],
        )
        order = [(b.id, b.page) for b in document.blocks] == [(b.id, b.page) for b in expected.blocks]
        record(
            "document_order",
            order,
            "按独立标注逐项核对内容块 ID、页码与顺序；不评估像素布局。",
            [output_path + "/blocks", reference_path + "/blocks"],
        )
        for key, kind in [
            ("document_text", "paragraph"),
            ("document_headings", "heading"),
            ("document_tables", "table"),
        ]:
            blocks = [b for b in expected.blocks if b.kind == kind]
            mismatches = [
                b.id for b in blocks if b.id not in by_id or signatures([by_id[b.id]]) != signatures([b])
            ]
            extras = [b.id for b in document.blocks if b.kind == kind and b.id not in {e.id for e in blocks}]
            locations = []
            for block in blocks:
                actual = by_id.get(block.id)
                if isinstance(block, Table) and isinstance(actual, Table):
                    for row_index, row in enumerate(block.rows):
                        for col_index, cell in enumerate(row):
                            if (
                                row_index >= len(actual.rows)
                                or col_index >= len(actual.rows[row_index])
                                or normalize(actual.rows[row_index][col_index]) != normalize(cell)
                            ):
                                locations.append(f"{block.id} 行{row_index + 1}列{col_index + 1}")
            explanation = f"{kind} 匹配 {len(blocks) - len(mismatches)}/{len(blocks)}；差异块 {mismatches or '无'}；多余块 {extras or '无'}。"
            if locations:
                explanation += "单元格差异：" + "、".join(locations[:10]) + "。"
            paths = [output_path + "/blocks", reference_path + "/blocks"]
            record(key, not mismatches and not extras, explanation, paths)
        if parsed.markdown is not None:
            try:
                consistent = markdown_signatures(parsed.markdown) == signatures(document.blocks)
                record(
                    "document_markdown",
                    consistent,
                    "按受限 Markdown 语法解析标题、段落和表格，与 JSON 内容顺序逐项比较。",
                    ["/output/markdown", output_path + "/blocks"],
                )
            except ValueError as exc:
                record("document_markdown", False, str(exc), ["/output/markdown"])
    elif not binding_error and output_error:
        record("document_completeness", False, "JSON 输出不符合文档合同：" + output_error, ["/output"])

    response["results"] = []
    for check in request["checks"]:
        key = check["id"]
        unknown = {
            "id": key,
            "status": "unknown",
            "evidence_paths": [],
            "explanation": binding_error
            or ("缺少有效 JSON/Markdown 输出。" if key.startswith("document_") else "未实现此规则。"),
        }
        if key in {"document_ocr", "document_formulas"}:
            unknown["explanation"] = "当前样例是数字 PDF，未执行 OCR 或公式评估；此项不判为通过。"
        elif key not in {
            "document_source",
            "document_completeness",
            "document_text",
            "document_headings",
            "document_order",
            "document_tables",
            "document_markdown",
        }:
            unknown["explanation"] = "未实现此规则。"
        response["results"].append(results.get(key, unknown))
    return response
