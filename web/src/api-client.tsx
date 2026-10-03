import { useState, type ReactNode } from "react";

let accessToken = "";

export function setAccessToken(token: string) {
  accessToken = token;
}

export async function apiFetch(path: string, options?: RequestInit) {
  const result = await fetch("/api" + path, {
    ...options,
    headers: {
      "X-Review-Request": "1",
      ...(accessToken ? { Authorization: "Bearer " + accessToken } : {}),
      ...options?.headers,
    },
  });
  if (!result.ok) {
    let message = await result.text();
    try {
      const parsed = JSON.parse(message);
      message = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch { /* plain error */ }
    throw new Error(message || `请求失败 (${result.status})`);
  }
  return result;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  return (await apiFetch(path, options)).json();
}

export function DownloadLink({ path, children, className = "button" }: {
  path: string; children: ReactNode; className?: string;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <span>
    <a className={className} href={"/api" + path} aria-disabled={busy} onClick={async e => {
      e.preventDefault();
      if (busy) return;
      setBusy(true);
      setError("");
      try {
        const response = await apiFetch(path);
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = url;
        link.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "report";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) { setError((e as Error).message); }
      finally { setBusy(false); }
    }}>{busy ? "正在导出…" : children}</a>
    {error && <span className="error-banner" role="alert">{error}</span>}
  </span>;
}
