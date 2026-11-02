import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowLeft,
  Braces,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Clock3,
  Hash,
  Layers3,
  Maximize2,
  RefreshCw,
  Search,
} from "lucide-react";
import { navigate } from "./navigation";
import { exportTrace, getTrace } from "./api";
import {
  CopyButton,
  ErrorState,
  JsonTree,
  Loading,
  Status,
} from "./components";
import {
  barGeometry,
  controlledStopMessage,
  dateTime,
  duration,
  hasActionableError,
  isLive,
  runLabels,
  timeBounds,
  visibleSpans,
} from "./trace";
import type { Json, Span } from "./types";

export function TraceDetail({
  runId,
  tenant,
  params,
}: {
  runId: string;
  tenant: string;
  params: URLSearchParams;
}) {
  const [live, setLive] = useState(true);
  const [notice, setNotice] = useState("");
  const [exporting, setExporting] = useState(false);
  const query = useQuery({
    queryKey: ["trace", tenant, runId],
    queryFn: ({ signal }) => getTrace(runId, tenant, signal),
    refetchOnWindowFocus: live,
    refetchInterval: (state) =>
      live && isLive(state.state.data) ? 2000 : false,
  });
  const data = query.data;
  const requestedRoot = params.get("trace_id");
  const root =
    data?.roots.find((span) => span.trace_id === requestedRoot) ||
    (!requestedRoot ? data?.roots[0] : undefined);
  const spans =
    data?.runs.filter((span) => span.trace_id === root?.trace_id) || [];
  const errors = spans.filter(hasActionableError).length;
  const reconstructed = spans.filter(
    (span) => span.timing_source === "reconstructed",
  ).length;
  async function download() {
    setExporting(true);
    try {
      await exportTrace(runId, tenant);
      setNotice("已导出当前 Run 的全部 Trace（保留脱敏与截断）。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }
  return (
    <main className="detail-page">
      <button
        className="back-button"
        onClick={() =>
          navigate({ run_id: null, trace_id: null, span_id: null })
        }
      >
        <ArrowLeft size={14} />
        返回 Traces
      </button>
      <div className="detail-heading">
        <div>
          <div className="eyebrow">TRACE DETAIL</div>
          <h1>
            {root?.name || "执行详情"}
            {root && <Status value={root.status} />}
          </h1>
          <div className="id-line">
            <span className="muted">RUN</span>
            <code>{runId}</code>
            <CopyButton text={runId} label="复制 Run ID" />
          </div>
        </div>
        <div className="heading-actions">
          <button
            className={`button live-toggle ${live ? "enabled" : ""}`}
            onClick={() => setLive(!live)}
          >
            <span className="small-dot" />
            {isLive(data) ? (live ? "实时 · 2s" : "刷新已暂停") : "执行已停止"}
          </button>
          <button
            className="icon-button bordered"
            aria-label="刷新详情"
            onClick={() => {
              void query.refetch();
            }}
          >
            <RefreshCw size={16} className={query.isFetching ? "spin" : ""} />
          </button>
          <button
            className="button"
            disabled={!data || exporting}
            onClick={() => {
              void download();
            }}
          >
            <ArrowDownToLine size={15} />
            {exporting ? "导出中…" : "导出 Run JSON"}
          </button>
        </div>
      </div>
      {notice && (
        <div className="notice" role="status">
          {notice}
          <button className="text-button" onClick={() => setNotice("")}>
            关闭
          </button>
        </div>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.isError && !data ? (
        <ErrorState
          error={query.error}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : (
        data && (
          <>
            {query.isError && (
              <div className="notice warning" role="alert">
                刷新失败，当前展示最近一次成功读取的数据。
                <button
                  className="text-button"
                  onClick={() => {
                    void query.refetch();
                  }}
                >
                  重试
                </button>
              </div>
            )}
            <div className="detail-metrics">
              <div>
                <span>
                  <Clock3 size={13} />
                  根节点耗时
                </span>
                <strong>{duration(root?.duration_ms ?? null)}</strong>
              </div>
              <div>
                <span>
                  <Layers3 size={13} />
                  Spans
                </span>
                <strong>{spans.length}</strong>
              </div>
              <div>
                <span>
                  <AlertTriangle size={13} />
                  错误节点
                </span>
                <strong className={errors ? "text-error" : ""}>{errors}</strong>
              </div>
              <div>
                <span>
                  <Activity size={13} />
                  业务状态
                </span>
                <strong
                  className={data.run_status === "FAILED" ? "text-error" : ""}
                >
                  {data.run_status ? runLabels[data.run_status] : "未知"}
                </strong>
              </div>
              <div className="started-metric">
                <span>开始时间</span>
                <strong>{root ? dateTime(root.start_time) : "—"}</strong>
              </div>
            </div>
            <div className="trace-context">
              <label>
                <Hash size={13} />
                <span>TRACE</span>
                <select
                  aria-label="选择根 Trace"
                  value={root?.trace_id || ""}
                  onChange={(event) =>
                    navigate({ trace_id: event.target.value, span_id: null })
                  }
                >
                  {!root && <option value="">未找到选定 Trace</option>}
                  {data.roots.map((item) => (
                    <option key={item.id} value={item.trace_id}>
                      {item.name} · {item.trace_id}
                    </option>
                  ))}
                </select>
              </label>
              {root && (
                <CopyButton text={root.trace_id} label="复制 Trace ID" />
              )}
              <div className="tags">
                {root?.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
            </div>
            {reconstructed > 0 && (
              <div className="timing-note">
                <Clock3 size={14} />
                <span>
                  {reconstructed} 个 LLM / Tool 节点为调用结束后补录。◆
                  表示记录时间，实际调用耗时未采集。
                </span>
              </div>
            )}
            {root ? (
              <TraceWorkspace
                key={root.trace_id}
                root={root}
                spans={spans}
                selectedId={params.get("span_id")}
                now={query.dataUpdatedAt}
              />
            ) : (
              <div className="panel empty-state">
                <Layers3 size={30} />
                <h3>
                  {requestedRoot ? "未找到选定的 Trace" : "还没有 Trace 节点"}
                </h3>
                <p>
                  {requestedRoot
                    ? "可以从上方选择这个 Run 中的其他 Trace。"
                    : isLive(data)
                      ? "任务尚未开始采集，页面将自动刷新。"
                      : "这个 Run 尚未保存 Trace 数据。"}
                </p>
              </div>
            )}
          </>
        )
      )}
    </main>
  );
}

function TraceWorkspace({
  root,
  spans,
  selectedId,
  now,
}: {
  root: Span;
  spans: Span[];
  selectedId: string | null;
  now: number;
}) {
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const [search, setSearch] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [zoom, setZoom] = useState(1);
  const selected =
    visibleSpans([root], new Set(), "", false).find(
      (span) => span.id === selectedId,
    ) || root;
  const visible = visibleSpans([root], collapsed, search, errorsOnly);
  const { start, total } = timeBounds(spans, now);
  const toggle = (id: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const select = (span: Span) =>
    navigate({ span_id: span.id, trace_id: root.trace_id }, true);
  return (
    <section className="workspace-panel">
      <div className="timeline-panel">
        <div className="workspace-toolbar">
          <label className="search-input compact">
            <Search size={14} />
            <input
              aria-label="搜索节点"
              placeholder="查找节点…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={errorsOnly}
              onChange={(event) => setErrorsOnly(event.target.checked)}
            />
            错误路径
          </label>
          <div className="toolbar-spacer" />
          <button
            className="icon-button"
            aria-label="展开全部"
            title="展开全部"
            onClick={() => setCollapsed(new Set())}
          >
            <ChevronsUpDown size={16} />
          </button>
          <button
            className="icon-button"
            aria-label="折叠全部"
            title="折叠全部"
            onClick={() => setCollapsed(new Set(spans.map((span) => span.id)))}
          >
            <ChevronsDownUp size={16} />
          </button>
          <select
            className="zoom-select"
            aria-label="时间轴缩放"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
          >
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={4}>4×</option>
          </select>
          <button
            className="icon-button"
            title="重置视图"
            aria-label="重置视图"
            onClick={() => {
              setZoom(1);
              setCollapsed(new Set());
              setSearch("");
              setErrorsOnly(false);
            }}
          >
            <Maximize2 size={14} />
          </button>
        </div>
        <div className="waterfall-scroll">
          <div className="waterfall" style={{ minWidth: 320 + 360 * zoom }}>
            <div className="waterfall-head">
              <div className="tree-heading">
                调用树 <span className="count-badge">{visible.length}</span>
              </div>
              <div className="axis">
                {[0, 1, 2, 3, 4].map((tick) => (
                  <span key={tick} style={{ left: `${tick * 25}%` }}>
                    {tick === 0 ? "0 ms" : duration((total * tick) / 4)}
                  </span>
                ))}
              </div>
            </div>
            {visible.map((span) => {
              const geometry = barGeometry(span, start, total, now);
              const replay = span.timing_source === "reconstructed";
              const controlledStop = controlledStopMessage(span.error);
              return (
                <div
                  key={span.id}
                  className={`waterfall-row ${selected.id === span.id ? "selected" : ""} ${hasActionableError(span) ? "error-row" : controlledStop ? "controlled-stop-row" : ""}`}
                  data-testid="span-row"
                  data-span-id={span.id}
                >
                  <div
                    className="tree-cell"
                    style={{ paddingLeft: 12 + Math.min(span.depth, 10) * 17 }}
                  >
                    {span.children.length ? (
                      <button
                        className="collapse-button"
                        aria-label={`${collapsed.has(span.id) ? "展开" : "折叠"} ${span.name}`}
                        aria-expanded={!collapsed.has(span.id)}
                        onClick={() => toggle(span.id)}
                      >
                        {collapsed.has(span.id) ? (
                          <ChevronRight size={12} />
                        ) : (
                          <ChevronDown size={12} />
                        )}
                      </button>
                    ) : (
                      <span className="leaf-spacer" />
                    )}
                    <button
                      className="span-button"
                      title={`${span.name} · ${span.run_type}`}
                      aria-pressed={selected.id === span.id}
                      onClick={() => select(span)}
                    >
                      <span className={`type-icon type-${span.run_type}`}>
                        {span.run_type === "llm" ? (
                          "✦"
                        ) : span.run_type === "tool" ? (
                          "⌁"
                        ) : (
                          <Layers3 size={12} />
                        )}
                      </span>
                      <span className="span-name">{span.name}</span>
                      {hasActionableError(span) && (
                        <AlertTriangle size={12} className="text-error" />
                      )}
                      <span
                        className={`span-time ${span.status === "running" ? "text-running" : ""}`}
                      >
                        {replay
                          ? "补录"
                          : span.status === "running"
                            ? "运行中"
                            : duration(span.duration_ms)}
                      </span>
                    </button>
                  </div>
                  <button
                    className="timeline-cell"
                    aria-label={`查看 ${span.name} 时间轴`}
                    onClick={() => select(span)}
                  >
                    <span className="grid-lines" />
                    {replay ? (
                      <span
                        className={`replay-marker type-${span.run_type} ${hasActionableError(span) ? "failed" : controlledStop ? "controlled" : ""}`}
                        style={{
                          left: `clamp(5px, ${geometry.left}%, calc(100% - 6px))`,
                        }}
                        title="补录记录时间；未采集真实耗时"
                      >
                        ◆
                      </span>
                    ) : (
                      <span
                        className={`time-bar type-${span.run_type} ${hasActionableError(span) ? "failed" : controlledStop ? "controlled" : ""} ${span.status === "running" ? "in-progress" : ""} ${span.status === "incomplete" ? "unfinished" : ""}`}
                        style={{
                          left: `min(${geometry.left}%, calc(100% - 4px))`,
                          width: `${geometry.width}%`,
                        }}
                        title={`${span.name} · ${duration(span.duration_ms)}`}
                      >
                        <span />
                      </span>
                    )}
                  </button>
                </div>
              );
            })}
            {!visible.length && (
              <div className="empty-state compact-empty">
                <Search size={24} />
                <p>没有匹配的节点</p>
                <button
                  className="text-button"
                  onClick={() => {
                    setSearch("");
                    setErrorsOnly(false);
                  }}
                >
                  清除节点筛选
                </button>
              </div>
            )}
          </div>
        </div>
        <div className="timeline-legend">
          <span className="legend-label">节点类型</span>
          {["chain", "llm", "tool", "retriever", "parser"].map((type) => (
            <span key={type}>
              <i className={`type-${type}`} />
              {type}
            </span>
          ))}
          <span>◆ 补录</span>
        </div>
      </div>
      <Inspector key={selected.id} span={selected} />
    </section>
  );
}

function Inspector({ span }: { span: Span }) {
  const [tab, setTab] = useState("inputs");
  const controlledStop = controlledStopMessage(span.error);
  const tabs = [
    { key: "inputs", label: "输入" },
    { key: "outputs", label: "输出" },
    { key: "extra", label: "Metadata" },
    { key: "error", label: controlledStop ? "停止原因" : "错误" },
    { key: "raw", label: "JSON" },
  ];
  const raw = { ...span, children: span.children.map((child) => child.id) };
  const value: Json =
    tab === "raw"
      ? (raw as unknown as Json)
      : tab === "error"
        ? controlledStop || span.error
        : span[tab as "inputs" | "outputs" | "extra"];
  const metadata = span.extra.metadata;
  const usage =
    metadata && typeof metadata === "object" && !Array.isArray(metadata)
      ? metadata.usage
      : null;
  return (
    <aside className="inspector" aria-label="节点详情">
      <div className="inspector-heading">
        <span>
          <Braces size={15} />
          节点详情
        </span>
        <CopyButton text={span.id} label="复制 Span ID" />
      </div>
      <div className="inspector-overview">
        <span className={`type-pill type-${span.run_type}`}>
          {span.run_type.toUpperCase()}
        </span>
        <h2>{span.name}</h2>
        <div className="inspector-status">
          {controlledStop ? (
            <span className="status controlled">
              <span className="status-dot" />
              受控停止
            </span>
          ) : (
            <Status value={span.status} />
          )}
          <span className="mono">
            {span.timing_source === "reconstructed"
              ? "耗时未采集"
              : duration(span.duration_ms)}
          </span>
        </div>
        <dl>
          <dt>Span ID</dt>
          <dd className="mono">{span.id}</dd>
          <dt>Parent ID</dt>
          <dd className="mono">{span.parent_run_id || "根节点"}</dd>
          <dt>开始时间</dt>
          <dd>{dateTime(span.start_time)}</dd>
          <dt>结束时间</dt>
          <dd>{span.end_time ? dateTime(span.end_time) : "尚未记录"}</dd>
        </dl>
        {span.tags.length > 0 && (
          <div className="tags">
            {span.tags.map((tag) => (
              <span className="tag" key={tag}>
                {tag}
              </span>
            ))}
          </div>
        )}
        {usage && (
          <div className="usage-block">
            <span>已记录的 Token 用量</span>
            <JsonTree value={usage} label="usage" />
          </div>
        )}
        {span.error && (
          <div className={`span-error ${controlledStop ? "controlled" : ""}`}>
            <AlertTriangle size={14} />
            <span>{controlledStop || span.error}</span>
          </div>
        )}
      </div>
      <div className="inspector-tabs" role="tablist" aria-label="节点数据">
        {tabs.map((item) => (
          <button
            key={item.key}
            id={`tab-${item.key}`}
            role="tab"
            aria-selected={tab === item.key}
            aria-controls="span-content"
            onClick={() => setTab(item.key)}
            className={tab === item.key ? "active" : ""}
          >
            {item.label}
            {item.key === "error" && span.error && !controlledStop && (
              <span className="small-dot" />
            )}
          </button>
        ))}
      </div>
      <div className="json-toolbar">
        <span>
          {tab === "raw"
            ? "节点 JSON · children 为 ID 引用"
            : tab === "error"
              ? controlledStop
                ? "停止原因"
                : "错误详情"
              : "JSON VIEWER"}
        </span>
        <CopyButton
          text={JSON.stringify(value, null, 2)}
          label="复制当前 JSON"
        />
      </div>
      <div
        id="span-content"
        className="inspector-content"
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        key={tab}
      >
        {tab === "error" && !span.error ? (
          <div className="no-error">这个节点未记录错误。</div>
        ) : (
          <JsonTree value={value} label={tab === "raw" ? "span" : tab} />
        )}
      </div>
      <div className="inspector-footnote">
        内容为存库快照，可能已脱敏或截断。
      </div>
    </aside>
  );
}
