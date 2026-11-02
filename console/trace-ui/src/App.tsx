import { useState, useSyncExternalStore } from "react";
import {
  Activity,
  ArrowUpRight,
  Braces,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Filter,
  History,
  Layers3,
  ListTodo,
  MessageSquarePlus,
  RefreshCw,
  Search,
  Settings2,
  X,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getSessions, getTraces } from "./api";
import { ErrorState, Loading, Status } from "./components";
import { dateTime, duration, runLabels } from "./trace";
import { TraceDetail } from "./TraceDetail";
import { NewRunPage, RunDetailPage, RunsPage, SessionsPage } from "./Operations";
import { cursorTrail, navigate, subscribeToLocation } from "./navigation";

export function App() {
  const search = useSyncExternalStore(
    subscribeToLocation,
    () => window.location.search,
  );
  const params = new URLSearchParams(search);
  const tenant =
    params.get("tenant") || import.meta.env.VITE_TRACE_TENANT || "local-tenant";
  const runId = params.get("run_id");
  const view = params.get("view") || "traces";
  const [tenantOpen, setTenantOpen] = useState(false);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          aria-label="返回 Trace 列表"
          onClick={() =>
            navigate({ view: "runs", run_id: null, trace_id: null, span_id: null })
          }
        >
          <span className="brand-mark">
            <Activity size={22} />
          </span>
          <span>
            enterprise<span className="brand-light">agent</span>
          </span>
        </button>
        <div className="workspace">
          <span className="workspace-avatar">EA</span>
          <div>
            <strong>EnterpriseAgent</strong>
            <span>观测工作空间</span>
          </div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <button
          className={`nav-item new-run-nav ${view === "new" ? "active" : ""}`}
          onClick={() => navigate({ view: "new", run_id: null })}
        >
          <MessageSquarePlus size={17} />
          <span>新聊天</span>
        </button>
        <RecentSessions tenant={tenant} activeSessionId={params.get("session_id")} />
        <button
          className={`nav-item ${view === "runs" || view === "run" ? "active" : ""}`}
          onClick={() => navigate({ view: "runs", run_id: null })}
        >
          <ListTodo size={17} />
          <span>Runs</span>
          <span className="nav-count">⌘1</span>
        </button>
        <button
          className={`nav-item ${view === "sessions" ? "active" : ""}`}
          onClick={() => navigate({ view: "sessions", run_id: null })}
        >
          <History size={17} />
          <span>历史对话</span>
        </button>
        <div className="nav-label">OBSERVABILITY</div>
        <button
          className={`nav-item ${view === "traces" ? "active" : ""}`}
          onClick={() =>
            navigate({ view: null, run_id: null, trace_id: null, span_id: null })
          }
        >
          <Layers3 size={17} />
          <span>Trace Explorer</span>
          <span className="nav-count">⌘</span>
        </button>
        <button className="nav-item muted-nav" onClick={() => setTenantOpen(true)}>
          <Settings2 size={17} />
          <span>工作空间设置</span>
        </button>
        <div className="sidebar-note">
          <Braces size={18} />
          <p>
            从请求到每一次调用。
            <br />
            看清 Agent 的执行过程。
          </p>
        </div>
        <div className="sidebar-bottom">
          <span className="small-dot" />
          <span>Trace Console</span>
          <span className="version">v0.1</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作空间</span>
            <ChevronRight size={13} />
            <button
              onClick={() =>
                navigate({ view: view === "traces" ? null : view, run_id: null, trace_id: null, span_id: null })
              }
            >
              {view === "traces"
                ? "Traces"
                : view === "new"
                  ? "新建任务"
                  : view === "sessions"
                    ? "历史对话"
                    : "Runs"}
            </button>
            {runId && (
              <>
                <ChevronRight size={13} />
                <span className="mono">{runId.slice(0, 12)}</span>
              </>
            )}
          </div>
          <button
            className="tenant-button"
            onClick={() => setTenantOpen(!tenantOpen)}
          >
            <span className="small-dot" />
            <span>{tenant}</span>
            <ChevronRight size={12} />
          </button>
        </header>
        {tenantOpen && (
          <form
            className="tenant-panel"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              const value = String(form.get("tenant")).trim();
              if (value) {
                navigate({
                  tenant: value,
                  run_id: null,
                  trace_id: null,
                  span_id: null,
                  cursor: null,
                });
                setTenantOpen(false);
              }
            }}
          >
            <div>
              <strong>开发租户</strong>
              <p>使用项目现有的开发身份请求头。</p>
            </div>
            <input
              aria-label="租户 ID"
              name="tenant"
              defaultValue={tenant}
              required
              maxLength={128}
            />
            <button className="button primary" type="submit">
              <Check size={14} />
              切换
            </button>
            <button
              className="icon-button"
              type="button"
              onClick={() => setTenantOpen(false)}
              aria-label="关闭租户设置"
            >
              <X size={16} />
            </button>
          </form>
        )}
        {view === "new" ? (
          <NewRunPage tenant={tenant} />
        ) : view === "sessions" ? (
          <SessionsPage tenant={tenant} params={params} />
        ) : view === "runs" ? (
          <RunsPage tenant={tenant} params={params} />
        ) : view === "run" && runId ? (
          <RunDetailPage
            tenant={tenant}
            runId={runId}
            sessionId={params.get("session_id") || undefined}
          />
        ) : runId ? (
          <TraceDetail
            key={`${tenant}:${runId}`}
            runId={runId}
            tenant={tenant}
            params={params}
          />
        ) : (
          <Explorer key={tenant} tenant={tenant} params={params} />
        )}
        <footer className="footer">
          <span>
            ENTERPRISEAGENT <span className="footer-divider">/</span>{" "}
            OBSERVABILITY
          </span>
          <span>时间显示为浏览器本地时间</span>
        </footer>
      </div>
    </div>
  );
}

function RecentSessions({
  tenant,
  activeSessionId,
}: {
  tenant: string;
  activeSessionId: string | null;
}) {
  const query = useQuery({
    queryKey: ["sessions", tenant],
    queryFn: ({ signal }) => getSessions(tenant, signal),
    refetchInterval: 10000,
  });
  const sessions = query.data?.items.slice(0, 8) || [];
  return (
    <div className="recent-sidebar" aria-label="最近聊天">
      <div className="nav-label recent-sidebar-label">最近</div>
      {sessions.map((session) => (
        <button
          className={`recent-sidebar-item ${activeSessionId === session.session_id ? "active" : ""}`}
          key={session.session_id}
          title={session.title}
          onClick={() =>
            navigate({
              view: "run",
              run_id: session.latest_run_id,
              session_id: session.session_id,
            })
          }
        >
          <History size={14} />
          <span>{session.title}</span>
        </button>
      ))}
      {!query.isPending && !sessions.length && (
        <span className="recent-sidebar-empty">暂无聊天记录</span>
      )}
    </div>
  );
}

function Explorer({
  tenant,
  params,
}: {
  tenant: string;
  params: URLSearchParams;
}) {
  const [live, setLive] = useState(true);
  const history = cursorTrail();
  const query = useQuery({
    queryKey: ["traces", tenant, params.toString()],
    queryFn: ({ signal }) => getTraces(params, tenant, signal),
    refetchInterval: live && !params.get("cursor") ? 10000 : false,
    refetchOnWindowFocus: live,
  });
  const rows = query.data?.items || [];
  const errors = rows.filter((row) => row.has_error).length;
  const finished = rows.filter((row) => row.duration_ms !== null);
  const average = finished.length
    ? finished.reduce((sum, row) => sum + row.duration_ms!, 0) / finished.length
    : null;
  const reset = () => {
    navigate({
      q: null,
      name: null,
      status: null,
      tag: null,
      has_error: null,
      start_time: null,
      end_time: null,
      cursor: null,
    });
  };
  return (
    <main className="explorer">
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            <span className="small-dot" />
            EXECUTION OBSERVABILITY
          </div>
          <h1>Trace Explorer</h1>
          <p>追踪每一次执行，理解每一个决策。</p>
        </div>
        <div className="heading-actions">
          <button
            className={`button live-toggle ${live ? "enabled" : ""}`}
            onClick={() => setLive(!live)}
          >
            <span className="small-dot" />
            {live ? "自动刷新 · 10s" : "自动刷新已暂停"}
          </button>
          <button
            className="button"
            onClick={() => {
              void query.refetch();
            }}
            aria-label="刷新列表"
          >
            <RefreshCw size={15} className={query.isFetching ? "spin" : ""} />
            刷新
          </button>
        </div>
      </div>
      <div className="stats-grid">
        <Stat
          label="当前页 Traces"
          value={query.data ? String(rows.length).padStart(2, "0") : "—"}
          caption="独立执行链路"
          icon={<Layers3 size={18} />}
        />
        <Stat
          label="当前页平均耗时"
          value={duration(average)}
          caption="仅统计有完整计时的根节点"
          icon={<Activity size={18} />}
        />
        <Stat
          label="包含错误的 Trace"
          value={query.data ? String(errors).padStart(2, "0") : "—"}
          caption="包含任意错误节点"
          icon={<CircleDot size={18} />}
          alert={errors > 0}
        />
        <Stat
          label="当前页 Spans"
          value={
            query.data
              ? String(rows.reduce((sum, row) => sum + row.span_count, 0))
              : "—"
          }
          caption="Chain · LLM · Tool"
          icon={<Braces size={18} />}
        />
      </div>
      <section className="panel explorer-panel">
        <div className="panel-title">
          <h2>
            执行记录{" "}
            <span className="count-badge">
              {query.data ? rows.length : "—"}
            </span>
          </h2>
          <span className="muted">按开始时间倒序</span>
        </div>
        <form
          key={params.toString()}
          className="filters"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const updates: Record<string, string | null> = { cursor: null };
            for (const key of [
              "q",
              "status",
              "tag",
              "start_time",
              "end_time",
            ]) {
              const value = String(data.get(key) || "").trim();
              updates[key] =
                value && key.endsWith("_time")
                  ? new Date(value).toISOString()
                  : value || null;
            }
            updates.has_error = data.has("has_error") ? "true" : null;
            navigate(updates);
          }}
        >
          <label className="search-input">
            <Search size={16} />
            <input
              name="q"
              defaultValue={params.get("q") || ""}
              placeholder="搜索 Run ID、Trace ID 或根节点名称…"
              aria-label="搜索 Trace"
            />
          </label>
          <select
            name="status"
            defaultValue={params.get("status") || ""}
            aria-label="状态筛选"
          >
            <option value="">全部节点状态</option>
            <option value="success">成功</option>
            <option value="error">错误</option>
            <option value="running">运行中</option>
            <option value="interrupted">已中断</option>
            <option value="incomplete">未闭合</option>
          </select>
          <input
            className="tag-filter"
            name="tag"
            defaultValue={params.get("tag") || ""}
            placeholder="根节点标签"
            aria-label="标签筛选"
          />
          <div className="date-filters">
            <input
              type="datetime-local"
              step="1"
              name="start_time"
              aria-label="开始时间"
              defaultValue={localInput(params.get("start_time"))}
            />
            <span>—</span>
            <input
              type="datetime-local"
              step="1"
              name="end_time"
              aria-label="结束时间"
              defaultValue={localInput(params.get("end_time"))}
            />
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              name="has_error"
              defaultChecked={params.get("has_error") === "true"}
            />
            仅含错误
          </label>
          <button className="button filter-submit" type="submit">
            <Filter size={14} />
            筛选
          </button>
          <button
            className="icon-button"
            title="重置筛选"
            aria-label="重置筛选"
            type="button"
            onClick={reset}
          >
            <X size={15} />
          </button>
        </form>
        {query.isPending ? (
          <Loading />
        ) : query.isError ? (
          <ErrorState
            error={query.error}
            retry={() => {
              void query.refetch();
            }}
          />
        ) : !rows.length ? (
          <div className="empty-state">
            <Layers3 size={32} />
            <h3>还没有匹配的 Trace</h3>
            <p>调整筛选或租户，或执行一次 Agent 任务后刷新。</p>
            <button className="button" onClick={reset}>
              清除筛选
            </button>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="trace-table">
              <thead>
                <tr>
                  <th>执行链路 / Run</th>
                  <th>根节点状态</th>
                  <th>业务状态</th>
                  <th>耗时</th>
                  <th>Spans</th>
                  <th>标签 / Route</th>
                  <th>开始时间</th>
                  <th>
                    <span className="sr-only">查看</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.trace_id}>
                    <td>
                      <a
                        className="trace-link"
                        href={`?${detailParams(params, row.run_id, row.trace_id)}`}
                        onClick={(event) => {
                          if (
                            !event.metaKey &&
                            !event.ctrlKey &&
                            !event.shiftKey &&
                            event.button === 0
                          ) {
                            event.preventDefault();
                            navigate({
                              run_id: row.run_id,
                              trace_id: row.trace_id,
                            });
                          }
                        }}
                      >
                        <span className="row-icon">
                          <Layers3 size={17} />
                        </span>
                        <span>
                          <strong>{row.root_name}</strong>
                          <span className="mono secondary">
                            {row.run_id.slice(0, 16)}…
                          </span>
                        </span>
                      </a>
                    </td>
                    <td>
                      <Status value={row.status} />
                      {row.error_count > 0 && (
                        <span className="error-count">
                          {row.error_count} 个错误节点
                        </span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`business-status ${row.run_status === "FAILED" ? "text-error" : ""}`}
                      >
                        {row.run_status ? runLabels[row.run_status] : "—"}
                      </span>
                    </td>
                    <td className="mono">{duration(row.duration_ms)}</td>
                    <td>
                      <span className="span-count">{row.span_count}</span>
                    </td>
                    <td>
                      <div className="tags">
                        {row.route && (
                          <span className="tag route-tag">{row.route}</span>
                        )}
                        {row.tags.slice(0, 2).map((tag) => (
                          <span className="tag" key={tag}>
                            {tag}
                          </span>
                        ))}
                        {!row.route && !row.tags.length && "—"}
                      </div>
                    </td>
                    <td className="date-cell">{dateTime(row.start_time)}</td>
                    <td>
                      <ArrowUpRight size={15} className="muted" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <span>
            {query.isFetching ? "正在同步…" : "每页最多 30 条"}
            <span className="footer-divider">·</span>一条记录对应一个根 Trace
          </span>
          <div>
            <button
              className="button small"
              disabled={!params.get("cursor")}
              onClick={() => {
                const previous = [...history];
                const cursor = previous.pop() || null;
                navigate({ cursor }, false, previous);
              }}
            >
              <ChevronLeft size={14} />
              {history.length ? "上一页" : "返回首页"}
            </button>
            <button
              className="button small"
              disabled={!query.data?.next_cursor || query.isFetching}
              onClick={() => {
                navigate({ cursor: query.data?.next_cursor || null }, false, [
                  ...history,
                  params.get("cursor") || "",
                ]);
              }}
            >
              下一页
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      </section>
      <div className="list-note">
        <Activity size={14} />
        <span>
          数据来自已持久化的
          Trace。历史输入和输出可能经过截断；节点状态与业务运行状态分别展示。
        </span>
      </div>
    </main>
  );
}
function Stat({
  label,
  value,
  caption,
  icon,
  alert = false,
}: {
  label: string;
  value: string;
  caption: string;
  icon: React.ReactNode;
  alert?: boolean;
}) {
  return (
    <div className={`stat-card ${alert ? "alert-stat" : ""}`}>
      <div className="stat-label">
        {label}
        {icon}
      </div>
      <div className="stat-value">{value}</div>
      <div className="stat-caption">{caption}</div>
    </div>
  );
}
function localInput(value: string | null) {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 19);
}
function detailParams(params: URLSearchParams, runId: string, traceId: string) {
  const next = new URLSearchParams(params);
  next.set("run_id", runId);
  next.set("trace_id", traceId);
  return next;
}
