import type { Span, TraceView } from "./types";

const controlledStopMessages: Record<string, string> = {
  evidence_sufficient: "研究循环已正常结束：已获得足够的可验证来源。",
  search_budget_exhausted: "研究已停止：搜索次数已达到本轮上限。",
  fetch_budget_exhausted:
    "研究已停止：网页抓取已达到本轮上限，尚未获得足够的可验证来源。",
  tool_budget_exhausted: "研究已停止：工具调用次数已达到本轮上限。",
  duplicate_call_limit: "研究已停止：重复工具调用已达到限制。",
};

export function controlledStopMessage(error: string | null): string | null {
  if (!error) return null;
  const reason = Object.keys(controlledStopMessages).find((key) =>
    error.includes(key),
  );
  return reason ? controlledStopMessages[reason] : null;
}

export function hasActionableError(span: Span): boolean {
  return Boolean(span.error && !controlledStopMessage(span.error));
}

export const statusLabels = {
  success: "成功",
  error: "错误",
  running: "运行中",
  interrupted: "已中断",
  incomplete: "未闭合",
};
export const runLabels = {
  PENDING: "等待执行",
  RUNNING: "运行中",
  WAITING_INPUT: "等待输入",
  SUCCEEDED: "成功",
  PARTIAL: "部分完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};
export function duration(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value < 1) return "< 1 ms";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(2)} s`;
  return `${(value / 60_000).toFixed(1)} min`;
}
export function dateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
export function isLive(view?: TraceView): boolean {
  if (!view) return false;
  if (view.run_status) return ["PENDING", "RUNNING"].includes(view.run_status);
  return view.runs.some((span) => span.status === "running");
}

export function visibleSpans(
  roots: Span[],
  collapsed: Set<string>,
  query: string,
  errorsOnly: boolean,
): Span[] {
  const output: Span[] = [];
  const search = query.toLowerCase().trim();
  const matches = (span: Span): boolean =>
    (!search ||
      `${span.name} ${span.id} ${span.run_type}`
        .toLowerCase()
        .includes(search)) &&
    (!errorsOnly || hasActionableError(span));
  const visit = (span: Span): { matches: boolean; spans: Span[] } => {
    const children = span.children.map(visit);
    const relevant = matches(span) || children.some((child) => child.matches);
    if (!relevant) return { matches: false, spans: [] };
    return {
      matches: true,
      spans: [
        span,
        ...(!collapsed.has(span.id) || Boolean(search) || errorsOnly
          ? children.flatMap((child) => child.spans)
          : []),
      ],
    };
  };
  for (const root of roots) output.push(...visit(root).spans);
  return output;
}

export function timeBounds(spans: Span[], now: number) {
  if (!spans.length) return { start: now, total: 1 };
  const start = Math.min(...spans.map((span) => Date.parse(span.start_time)));
  const end = Math.max(
    ...spans.map((span) =>
      span.end_time
        ? Date.parse(span.end_time)
        : span.status === "running"
          ? now
          : Date.parse(span.start_time),
    ),
  );
  return { start, total: Math.max(1, end - start) };
}
export function barGeometry(
  span: Span,
  start: number,
  total: number,
  now: number,
) {
  const left = Math.max(
    0,
    Math.min(100, ((Date.parse(span.start_time) - start) / total) * 100),
  );
  const end = span.end_time
    ? Date.parse(span.end_time)
    : span.status === "running"
      ? now
      : Date.parse(span.start_time);
  return {
    left,
    width: Math.max(
      0,
      Math.min(100 - left, ((end - Date.parse(span.start_time)) / total) * 100),
    ),
  };
}
