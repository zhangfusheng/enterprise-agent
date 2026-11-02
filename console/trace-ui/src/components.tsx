import { useState } from "react";
import {
  AlertCircle,
  Check,
  ChevronRight,
  Copy,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import type { Json, TraceStatus } from "./types";
import { statusLabels } from "./trace";

export function Status({ value }: { value: TraceStatus }) {
  return (
    <span className={`status ${value}`}>
      <span className="status-dot" />
      {statusLabels[value]}
    </span>
  );
}
export function CopyButton({
  text,
  label = "复制",
}: {
  text: string;
  label?: string;
}) {
  const [result, setResult] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setResult("已复制");
    } catch {
      setResult("复制失败，请手动选择文本");
    }
    setTimeout(() => setResult(""), 2400);
  }
  return (
    <button
      className="icon-button copy-button"
      title={result || label}
      aria-label={result || label}
      onClick={copy}
    >
      {result === "已复制" ? <Check size={14} /> : <Copy size={14} />}
      <span className="sr-only" aria-live="polite">
        {result}
      </span>
    </button>
  );
}
export function Loading() {
  return (
    <div className="empty-state" role="status">
      <LoaderCircle className="spin" size={26} />
      <h3>正在读取 Trace…</h3>
    </div>
  );
}
export function ErrorState({
  error,
  retry,
}: {
  error: Error;
  retry: () => void;
}) {
  return (
    <div className="empty-state error-state" role="alert">
      <AlertCircle size={28} />
      <h3>暂时无法读取数据</h3>
      <p>{error.message}</p>
      <button className="button" onClick={retry}>
        <RefreshCw size={14} />
        重试
      </button>
    </div>
  );
}
export function JsonTree({
  value,
  label = "root",
  depth = 0,
}: {
  value: Json;
  label?: string;
  depth?: number;
}) {
  if (typeof value !== "object" || value === null)
    return (
      <div className="json-line">
        <span className="json-key">{label}: </span>
        <span className={`json-${value === null ? "null" : typeof value}`}>
          {JSON.stringify(value)}
        </span>
      </div>
    );
  const entries = Object.entries(value);
  const array = Array.isArray(value);
  if (!entries.length)
    return (
      <div className="json-line">
        <span className="json-key">{label}: </span>
        {array ? "[]" : "{}"}
      </div>
    );
  return (
    <details className="json-node" open={depth < 2}>
      <summary>
        <ChevronRight size={12} />
        <span className="json-key">{label}</span>{" "}
        <span className="muted">
          {array ? `[${entries.length}]` : `{${entries.length}}`}
        </span>
      </summary>
      <div>
        {entries.map(([key, child]) => (
          <JsonTree key={key} value={child} label={key} depth={depth + 1} />
        ))}
      </div>
    </details>
  );
}
