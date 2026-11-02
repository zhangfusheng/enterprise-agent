import { describe, expect, it } from "vitest";
import {
  barGeometry,
  controlledStopMessage,
  duration,
  hasActionableError,
  isLive,
  timeBounds,
  visibleSpans,
} from "./trace";
import type { Span } from "./types";

function span(id: string, options: Partial<Span> = {}): Span {
  return {
    id,
    trace_id: "root",
    parent_run_id: null,
    name: id,
    run_type: "chain",
    start_time: "2026-01-01T00:00:00Z",
    end_time: "2026-01-01T00:00:02Z",
    inputs: {},
    outputs: {},
    extra: {},
    error: null,
    tags: [],
    dotted_order: id,
    children: [],
    depth: 0,
    has_error: false,
    status: "success",
    duration_ms: 2000,
    timing_source: "measured",
    ...options,
  };
}

describe("trace tree navigation", () => {
  const failure = span("failure", { error: "boom", depth: 2 });
  const root = span("root", {
    children: [
      span("parent", { depth: 1, children: [failure] }),
      span("sibling"),
    ],
  });
  it("keeps ancestors for search and error paths even when collapsed", () => {
    expect(
      visibleSpans([root], new Set(["root"]), "failure", false).map(
        (node) => node.id,
      ),
    ).toEqual(["root", "parent", "failure"]);
    expect(
      visibleSpans([root], new Set(), "", true).map((node) => node.id),
    ).toEqual(["root", "parent", "failure"]);
    expect(
      visibleSpans([root], new Set(["root"]), "", false).map((node) => node.id),
    ).toEqual(["root"]);
    expect(visibleSpans([root], new Set(), "missing", false)).toEqual([]);
  });

  it("translates bounded research stops without treating them as actionable errors", () => {
    const stopped = span("stopped", {
      error: "ResearchLoopStopped: fetch_budget_exhausted",
    });
    expect(controlledStopMessage(stopped.error)).toBe(
      "研究已停止：网页抓取已达到本轮上限，尚未获得足够的可验证来源。",
    );
    expect(hasActionableError(stopped)).toBe(false);
    expect(hasActionableError(failure)).toBe(true);
  });
});
describe("time and completion semantics", () => {
  it("handles zero-duration, missing durations and running spans", () => {
    expect(duration(null)).toBe("—");
    expect(duration(0)).toBe("< 1 ms");
    expect(duration(2500)).toBe("2.50 s");
    const root = span("root");
    const bounds = timeBounds([root], Date.parse(root.end_time!));
    expect(bounds.total).toBe(2000);
    expect(barGeometry(root, bounds.start, bounds.total, 0)).toEqual({
      left: 0,
      width: 100,
    });
    const point = span("point", {
      start_time: root.end_time!,
      end_time: root.end_time,
    });
    expect(barGeometry(point, bounds.start, bounds.total, 0)).toEqual({
      left: 100,
      width: 0,
    });
  });
  it("polls pending runs with no spans and stops for terminated runs with open spans", () => {
    expect(
      isLive({ run_id: "r", run_status: "PENDING", runs: [], roots: [] }),
    ).toBe(true);
    expect(
      isLive({
        run_id: "r",
        run_status: "FAILED",
        runs: [span("open", { end_time: null, status: "running" })],
        roots: [],
      }),
    ).toBe(false);
  });
});
