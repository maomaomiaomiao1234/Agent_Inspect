"""Per-field accounting. Aggregates replace call sums, never add to them."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BASE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cost_usd")
KEYS = (*BASE_KEYS, "cache_read_tokens", "cache_write_tokens", "cache_miss_tokens")


class UsageField(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    value: int | float | None = Field(default=None, ge=0)
    status: Literal["complete", "partial", "unknown", "not_applicable"]
    source: Literal["aggregate", "calls", "gateway", "mixed", "none", "offline"]
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def consistent_value(self):
        if (self.value is None) != (self.status in {"unknown", "not_applicable"}):
            raise ValueError("完整/部分用量需要数值；未知/不适用不能提供数值。")
        return self


class UsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provenance: Literal["target_reported", "exporter_reported", "gateway_reported", "mixed"] = "target_reported"
    fields: dict[Literal["input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cost_usd",
                         "cache_read_tokens", "cache_write_tokens", "cache_miss_tokens"], UsageField]

    @model_validator(mode="after")
    def all_fields(self):
        if not set(BASE_KEYS).issubset(self.fields):
            raise ValueError("用量摘要需要逐字段声明完整性。")
        for key, field in self.fields.items():
            if key != "cost_usd" and field.value is not None and not isinstance(field.value, int):
                raise ValueError("Token 用量必须为整数。")
        parts = [self.fields[k] for k in ("input_tokens", "output_tokens", "total_tokens")]
        if all(f.status == "complete" for f in parts) and parts[2].value != parts[0].value + parts[1].value:
            raise ValueError("完整总 Token 必须等于完整输入与输出之和。")
        return self


def usage_value(usage, key):
    if key == "cost_usd":
        return (usage or {}).get(key)
    tokens = (usage or {}).get("tokens") or {}
    value = tokens.get(key.removesuffix("_tokens"))
    if key == "total_tokens" and value is None:
        if tokens.get("input") is not None and tokens.get("output") is not None:
            value = tokens["input"] + tokens["output"]
    return value


def _field(value, status, source, reason):
    return {"value": value, "status": status, "source": source, "reason": reason}


def turn_usage(turn):
    if turn.get("gateway") is not None:
        usage = turn_usage({"usage_mode": "model", "trace": {"coverage": "partial", "events": turn["gateway"]["events"]}})
        usage["provenance"] = "gateway_reported"
        for field in usage["fields"].values():
            if field["value"] is not None:
                field["source"] = "gateway"
            field["reason"] = "网关持久化的供应商用量；仅覆盖经过网关的请求，不与目标自报相加。"
        return usage
    fields = {}
    trace = turn.get("trace") or {}
    calls = [e for e in trace.get("events", []) if e["kind"] == "llm"]
    for key in KEYS:
        if turn.get("usage_mode") == "offline":
            fields[key] = _field(None, "not_applicable", "offline", "目标声明离线校准，未调用模型服务。")
            continue
        aggregate = usage_value(turn.get("usage"), key)
        if "aggregate_fields" in turn and key not in turn["aggregate_fields"]:
            aggregate = None  # The contract filled this field from calls; keep its actual source.
        values = [usage_value(e.get("usage"), key) for e in calls]
        known = [v for v in values if v is not None]
        if aggregate is not None:
            fields[key] = _field(aggregate, "complete", "aggregate", "目标上报的本轮汇总；不与逐次调用重复相加。")
        elif known:
            complete = trace.get("coverage") == "complete" and len(known) == len(calls) and all(
                e.get("context", {}).get("usage_complete") is not False for e in calls)
            fields[key] = _field(round(sum(known), 8) if key == "cost_usd" else sum(known),
                                 "complete" if complete else "partial", "calls",
                                 "完整逐次调用用量。" if complete else "部分调用或字段未采集，仅表示已观测下界。")
        else:
            reasons = sorted({e.get("context", {}).get("usage_missing_reason") for e in calls
                              if isinstance(e.get("context", {}).get("usage_missing_reason"), str)})
            reason = ("采集器报告：" + "、".join(reasons) if reasons else
                      "已记录模型调用但未上报此字段；无法区分供应商缺失与适配器漏采。" if calls else
                      "目标未上报此字段；需在模型调用处采集 usage。")
            fields[key] = _field(None, "unknown", "none", reason)
    return {"provenance": "target_reported", "fields": fields}


def combine_usage(summaries, *, complete=True, expected=None):
    fields = {}
    missing = expected is not None and len(summaries) != expected
    for key in KEYS:
        items = [s["fields"].get(key, _field(None, "unknown", "none", "历史记录未保存此细分字段。"))
                 for s in summaries]
        applicable = [f for f in items if f["status"] != "not_applicable"]
        known = [f["value"] for f in applicable if f["value"] is not None]
        if items and not applicable and complete and not missing:
            fields[key] = _field(None, "not_applicable", "offline", "目标声明离线校准，未调用模型服务。")
        elif known:
            full = complete and not missing and all(f["status"] == "complete" for f in applicable)
            sources = {f["source"] for f in applicable if f["value"] is not None}
            source = next(iter(sources)) if len(sources) == 1 else "mixed"
            value = round(sum(known), 8) if key == "cost_usd" else sum(known)
            reason = "完整汇总；每轮仅选一个用量来源。" if full else (
                "执行未完成或仍有未完成案例，仅表示已观测下界。" if not complete or missing
                else "部分调用或字段未采集，仅表示已观测下界。")
            fields[key] = _field(value, "complete" if full else "partial", source, reason)
        else:
            reasons = list(dict.fromkeys(f["reason"] for f in items))
            reason = "尚无返回的用量证据。" if not items else "；".join(reasons)[:1000]
            if items and not applicable:
                reason = "执行未完成，未观测轮次的模式和用量未知。"
            fields[key] = _field(None, "unknown", "none", reason)
    provenance = {summary.get("provenance", "target_reported") for summary in summaries}
    return {"provenance": next(iter(provenance)) if len(provenance) == 1 else "mixed" if provenance else "target_reported",
            "fields": fields}


def summarize_usage(turns, execution_complete=True):
    return combine_usage([turn_usage(t) for t in turns], complete=execution_complete)


def format_usage(usage, key):
    field = (usage or {}).get("fields", {}).get(key)
    if field:
        if field["status"] == "not_applicable":
            return "不适用（离线校准）"
        if field["value"] is not None:
            return ("≥" if field["status"] == "partial" else "") + str(field["value"])
    value = (usage or {}).get(key)
    return "未知" if value is None else str(value)
