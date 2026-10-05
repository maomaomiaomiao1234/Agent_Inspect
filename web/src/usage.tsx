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
