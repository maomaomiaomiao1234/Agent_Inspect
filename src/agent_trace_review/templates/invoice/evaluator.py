"""Example standalone evaluator: python3 evaluator.py request.json results.json."""

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


def evaluate(request):
    response = {key: request[key] for key in ("protocol", "run_id", "input_hash", "profile_hash")}
    results = []
    for check in request["checks"]:
        result = {
            "id": check["id"],
            "status": "unknown",
            "explanation": "未实现此规则。",
            "evidence_paths": [],
        }
        if check["id"] == "invoice_total":
            try:
                actual = Decimal(str(request["context"]["output"]["amount"]))
                expected = Decimal(str(request["context"]["artifacts"]["reference_invoice"]["amount"]))
                if not actual.is_finite() or not expected.is_finite():
                    raise ValueError("Non-finite amount")
                result.update(
                    status="pass" if actual == expected else "fail",
                    explanation="以 Decimal 比较提取金额与用户提供的独立参考金额。",
                    evidence_paths=["/output/amount", "/artifacts/reference_invoice/amount"],
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                result["explanation"] = "缺少有效金额或独立参考，无法验收。"
        results.append(result)
    response["results"] = results
    return response


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python3 evaluator.py request.json results.json")
    request = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(evaluate(request), ensure_ascii=False, indent=2) + "\n")
