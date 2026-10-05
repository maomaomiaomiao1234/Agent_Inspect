export type Usage = {
  input_tokens?: number | null; output_tokens?: number | null; total_tokens?: number | null;
  cost_usd?: number | null;
  fields?: Record<string, { value: number | null; status: "complete" | "partial" | "unknown" | "not_applicable";
    source: string; reason: string }>;
};

export function UsageValue({ usage, field, fallback }: { usage?: Usage; field: string; fallback?: number | null }) {
  const item = usage?.fields?.[field];
  const value = item?.value ?? usage?.[field as keyof Omit<Usage, "fields">] ?? fallback;
  const text = item?.status === "not_applicable" ? "不适用（离线校准）" : value == null ? "未知" :
    `${item?.status === "partial" ? "≥" : ""}${value}`;
  return <span title={item ? `${item.source} · ${item.reason}` : "历史记录未保存字段完整性。"}>{text}</span>;
}

export function ModelCallUsage({ data }: { data: Record<string, unknown> }) {
  const tokens = (data.usage as { tokens?: Record<string, number | null> } | null)?.tokens || {};
  const context = (data.context as Record<string, unknown> | null) || {};
  const labels: Record<string, string> = { input: "输入", output: "输出", total: "总量", reasoning: "推理",
    cache_read: "缓存命中", cache_write: "缓存写入", cache_miss: "缓存未命中" };
  const reasons: Record<string, string> = { provider_usage_missing: "供应商未返回用量", stream_interrupted: "流未完整结束",
    request_failed: "请求失败，保留已知用量", invalid_or_oversized_response: "部分响应未能采集" };
  return <>
    {context.collection === "model_gateway" && <span>网关采集 · 供应商报告</span>}
    <span>{Object.entries(labels).filter(([key]) => key === "input" || key === "output" || tokens[key] != null)
      .map(([key, label]) => `${label} Token ${tokens[key] == null ? "未知" : `${context.usage_complete === false ? "≥" : ""}${tokens[key]}`}`).join(" · ")}</span>
    {typeof context.usage_missing_reason === "string" && <span>{reasons[context.usage_missing_reason] || context.usage_missing_reason}</span>}
    {typeof context.provider_request_id === "string" && <span>供应商请求 ID {context.provider_request_id}</span>}
    {typeof context.response_id === "string" && <span>响应 ID {context.response_id}</span>}
  </>;
}
