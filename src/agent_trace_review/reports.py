from defusedxml.ElementTree import fromstring

from .models import Evaluation, Run, Verification
from .util import canonical, digest


def junit_report(content: bytes, check_id: str, state_hash: str, suite_hash: str, phase="final") -> dict:
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("JUnit 报告超过 20 MB。")
    try:
        root = fromstring(content)
    except Exception as exc:
        raise ValueError("无法解析安全的 JUnit XML。") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit 根节点必须为 testsuite 或 testsuites。")
    cases = {}
    for case in root.iter("testcase"):
        name = case.get("classname", "") + "::" + case.get("name", "unnamed")
        if name in cases:
            raise ValueError(f"JUnit 中用例 ID 重复，无法进行可靠回归比较：{name}")
        status = (
            "error"
            if case.find("error") is not None
            else "fail"
            if case.find("failure") is not None
            else "skipped"
            if case.find("skipped") is not None
            else "pass"
        )
        cases[name] = status
    passed = sum(v == "pass" for v in cases.values())
    failed = sum(v in {"fail", "error"} for v in cases.values())
    skipped = sum(v == "skipped" for v in cases.values())
    # A suite-level error must not be erased by successful child testcases.
    suites = list(root.iter("testsuite")) + ([root] if root.tag == "testsuites" else [])
    suite_errors = any(int(suite.get("errors", "0")) > 0 for suite in suites)
    suite_failures = any(int(suite.get("failures", "0")) > 0 for suite in suites)
    for suite in suites:
        if suite.get("tests") is not None and int(suite.get("tests")) != sum(
            1 for _ in suite.iter("testcase")
        ):
            raise ValueError("JUnit tests 计数与 testcase 集合不一致，无法确认完整执行。")
    result = (
        "error"
        if suite_errors or "error" in cases.values()
        else "fail"
        if failed or suite_failures
        else "pass"
        if passed
        else "unknown"
    )
    return Verification(
        id="junit_" + digest(content)[:16],
        check_id=check_id,
        provenance="external_verifier",
        result=result,
        phase=phase,
        state_hash=state_hash,
        suite_hash=suite_hash,
        executed=passed + failed,
        passed=passed,
        failed=failed,
        skipped=skipped,
        cases=cases,
        source_text=content.decode(),
    ).model_dump()


def markdown_report(run: Run, evaluation: Evaluation) -> str:
    lines = [
        f"# {run.title}",
        "",
        f"Run: `{run.id}` · Evaluation: `{evaluation.id}`",
        "",
        f"**结果：{evaluation.outcome}** — {evaluation.outcome_reason}",
        "",
        f"模型：{', '.join(run.models) or '未知'}",
        f"轨迹来源：{run.framework} · {'示例，不用于真实 Agent 能力排名' if run.demo else '用户提供的运行记录'}",
        (
            f"任务规则：{evaluation.custom['profile']['id']} · {evaluation.custom['profile_hash']}"
            if evaluation.custom
            else "任务规则：未提供自定义 Profile"
        ),
        "",
        "## 如何理解结论",
        "",
        "- 结果：按任务规则和匹配的验收材料判断；命令退出码 0 或 Agent 自报成功不代表任务完成。",
        "- 过程：诊断仅覆盖可见记录；没有触发规则不代表没有问题，也不是能力总分。",
        "- 资源：区分 observed（观测）、derived（推导）、partial（部分）与 unknown（未知）；缺失不填零。",
        "- 范围：单次运行不能代表 Agent 总体能力；证据不足时保留 inconclusive，不等同失败。",
        *(["- 示例不代表真实 Agent 能力；具体验证材料来源见记录。"] if run.demo else []),
        "",
        "## 指标",
        "",
        "| 指标 | 数值 | 证据状态 |",
        "| --- | --- | --- |",
    ]
    for metric in evaluation.metrics:
        lines.append(
            f"| {metric.label} | {metric.value if metric.value is not None else '未知'} {metric.unit} | {metric.status} |"
        )
    if isinstance(run.artifacts.get("source_description"), str):
        lines += ["", "材料来源声明：" + run.artifacts["source_description"], ""]
    if run.framework == "code-repair-fixture":
        lines += ["耗时覆盖模拟实验及容器验证，不是被测 Agent 耗时；未调用模型，Token 和费用保持未知。", ""]
    if run.framework == "document-conversion-fixture":
        lines += [
            "文档验收范围：固定两页英文数字 PDF 的正文、标题层级、块顺序、矩形表格、完整性和 Markdown/JSON 一致性。",
            "OCR、公式、合并单元格、版式保真未覆盖；不是官方 TEDS/CDM 或真实转换 Agent 成绩。",
            "",
        ]
    if evaluation.custom:
        lines += ["", "## 任务验收", "", "| 检查 | 必需 | 结果 |", "| --- | --- | --- |"]
        for check in evaluation.custom["checks"]:
            lines.append(
                f"| {check['description'] or check['id']} | {'是' if check['required'] else '否'} | {check['status']} |"
            )
    if run.framework == "http-target-v1" and isinstance(run.artifacts.get("assessment"), dict):
        assessment = run.artifacts["assessment"]
        lines += ["", "## 主动评测记录", "",
                  f"评测 ID：{assessment.get('job_id', '未知')}；案例：{assessment.get('case_id', '未知')}。",
                  f"源码提交（未认证部署绑定）：{assessment.get('commit') or '未知'}。",
                  "对话由评审端通过 HTTP 实际收集；内部工具调用、模型 Token 和费用未由该轨迹证明。",
                  "源码位置由题集配置或 README 声明关联，属于定位线索，不能据此确定故障原因。", ""]
        sources = assessment.get("source_evidence", [])
        if isinstance(sources, list):
            for source in sources[:40]:
                if isinstance(source, dict):
                    lines.append(f"- {source.get('path', '未知')}:{source.get('line', '?')} · {source.get('kind', 'source')}")
    if evaluation.verifications:
        lines += [
            "",
            "## 验证记录",
            "",
            "| 验收项 | 阶段 | 结果 | 执行 / 通过 / 失败 |",
            "| --- | --- | --- | --- |",
        ]
        for record in evaluation.verifications:
            counts = " / ".join(
                str(n) if n is not None else "未知" for n in [record.executed, record.passed, record.failed]
            )
            lines.append(f"| {record.check_id or record.id} | {record.phase} | {record.result} | {counts} |")
    if run.source_format == "generic" and run.diff_provided:
        lines += ["", "## 最终 diff", "", "```diff", run.diff or "（最终代码与基线相同）", "```", ""]
    lines += ["", "## 诊断", ""]
    if not evaluation.findings:
        lines += ["在已观察的范围内，没有触发当前规则。此结果不等于证明没有问题。", ""]
    for finding in evaluation.findings:
        lines += [
            f"### {finding.title} [{finding.verdict}]",
            "",
            finding.explanation,
            "",
            "证据：" + ", ".join(f"`{r}`" for r in finding.evidence_ids),
            "",
            "限制：" + "；".join(finding.limitations),
            "",
            finding.recommendation,
            "",
        ]
    lines += ["## 证据索引", ""]
    for evidence in evaluation.evidence:
        lines.append(
            f"- `{evidence.id}`: {evidence.description} · {evidence.pointer or evidence.kind} · artifact `{evidence.artifact_id or 'derived'}`"
        )
    lines += ["", "## 观察范围", ""]
    if evaluation.judge and evaluation.judge.get("backend") == "chat-completions":
        lines += [
            f"评审模型：{evaluation.judge['model']}；模型判断不等于独立事实核验。",
            f"评审用量（与被评 Agent 分开）：{canonical(evaluation.judge.get('usage'))}；费用未知。",
            "",
        ]
    lines += [f"- {key}: {value['level']} — {value['reason']}" for key, value in run.coverage.items()]
    lines += ["", "此报告分析已有导出材料；没有执行轨迹中的命令。", ""]
    return "\n".join(lines)
