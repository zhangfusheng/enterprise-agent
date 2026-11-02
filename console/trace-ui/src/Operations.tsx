import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  CircleDot,
  Clock3,
  FileText,
  ListFilter,
  LoaderCircle,
  MessageCircle,
  MessageSquarePlus,
  PauseCircle,
  Play,
  RefreshCw,
  Search,
  Send,
  Server,
  SquareTerminal,
  XCircle,
} from "lucide-react";
import {
  addRunInput,
  cancelRun,
  createRun,
  downloadArtifact,
  getArtifacts,
  getModels,
  getRun,
  getSession,
  getSessions,
  getRuns,
  resumeRun,
  streamRunEvents,
} from "./api";
import { ErrorState, Loading } from "./components";
import { dateTime, runLabels } from "./trace";
import { navigate } from "./navigation";
import type {
  Artifact,
  ModelOption,
  RunEvent,
  RunStatus,
  RunView,
  ReasoningEffort,
} from "./types";

const activeStatuses: RunStatus[] = ["PENDING", "RUNNING", "WAITING_INPUT"];

export function SessionsPage({
  tenant,
  params,
}: {
  tenant: string;
  params: URLSearchParams;
}) {
  const query = useQuery({
    queryKey: ["sessions", tenant],
    queryFn: ({ signal }) => getSessions(tenant, signal),
    refetchInterval: 10000,
  });
  const sessions = query.data?.items || [];
  const search = (params.get("session_q") || "").trim().toLowerCase();
  const visible = sessions.filter((session) => {
    if (!search) return true;
    return session.title.toLowerCase().includes(search);
  });
  return (
    <main className="operations-page sessions-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            <span className="small-dot" />
            CONVERSATION HISTORY
          </div>
          <h1>历史对话</h1>
          <p>每次新建任务都会保存为一个独立 session，可随时回来继续。</p>
        </div>
        <div className="heading-actions">
          <button className="button" onClick={() => void query.refetch()}>
            <RefreshCw size={15} className={query.isFetching ? "spin" : ""} />
            刷新历史
          </button>
          <button className="button primary" onClick={() => navigate({ view: "new", run_id: null })}>
            <MessageSquarePlus size={15} />
            新建对话
          </button>
        </div>
      </div>
      <section className="panel operations-panel session-history-panel">
        <div className="panel-title">
          <h2>
            你的 sessions <span className="count-badge">{visible.length}</span>
          </h2>
          <span className="muted">最近更新在前</span>
        </div>
        <form
          className="operations-filters"
          key={params.get("session_q") || ""}
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            navigate({ session_q: String(data.get("session_q") || "").trim() || null });
          }}
        >
          <label className="search-input">
            <Search size={16} />
            <input
              name="session_q"
              defaultValue={params.get("session_q") || ""}
              placeholder="搜索历史对话…"
              aria-label="搜索历史对话"
            />
          </label>
          <button className="button filter-submit" type="submit">
            <ListFilter size={14} />
            搜索
          </button>
        </form>
        {query.isPending && !sessions.length ? (
          <Loading />
        ) : query.isError && !sessions.length ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : !visible.length ? (
          <div className="empty-state">
            <MessageCircle size={32} />
            <h3>{search ? "没有匹配的历史对话" : "还没有历史对话"}</h3>
            <p>完成一次任务后，对话会自动保存在这里。</p>
            <button className="button primary" onClick={() => navigate({ view: "new" })}>
              <MessageSquarePlus size={14} />
              新建对话
            </button>
          </div>
        ) : (
          <div className="session-list">
            {visible.map((session) => {
              return (
                <button
                  className="session-row"
                  key={session.session_id}
                  onClick={() => navigate({ view: "run", run_id: session.latest_run_id, session_id: session.session_id })}
                >
                  <span className="session-row-icon"><MessageCircle size={17} /></span>
                  <span className="session-row-main">
                    <strong>{session.title}</strong>
                    <span>继续这段对话</span>
                  </span>
                  <span className="session-row-meta">
                    <time>{dateTime(session.updated_at)}</time>
                  </span>
                  <ArrowUpRight size={15} className="muted" />
                </button>
              );
            })}
          </div>
        )}
      </section>
      <div className="list-note">
        <HistoryNote />
        <span>聊天会话和消息均来自服务端持久化记录，并按当前用户隔离。</span>
      </div>
    </main>
  );
}

function HistoryNote() {
  return <MessageCircle size={14} />;
}

export function RunsPage({
  tenant,
  params,
}: {
  tenant: string;
  params: URLSearchParams;
}) {
  const [live, setLive] = useState(true);
  const query = useQuery({
    queryKey: ["runs", tenant, params.get("q"), params.get("status")],
    queryFn: ({ signal }) => getRuns(params, tenant, signal),
    refetchInterval: live ? 5000 : false,
  });
  const rows = query.data?.items || [];
  const active = rows.filter((row) => activeStatuses.includes(row.status)).length;
  const failed = rows.filter((row) => row.status === "FAILED").length;
  return (
    <main className="operations-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            <span className="small-dot" />
            AGENT OPERATIONS
          </div>
          <h1>Runs</h1>
          <p>提交任务、观察执行过程，并在需要时介入。</p>
        </div>
        <div className="heading-actions">
          <button
            className={`button live-toggle ${live ? "enabled" : ""}`}
            onClick={() => setLive(!live)}
          >
            <span className="small-dot" />
            {live ? "实时同步 · 5s" : "同步已暂停"}
          </button>
          <button
            className="button"
            aria-label="刷新任务列表"
            onClick={() => void query.refetch()}
          >
            <RefreshCw size={15} className={query.isFetching ? "spin" : ""} />
            刷新
          </button>
          <button
            className="button primary"
            onClick={() => navigate({ view: "new", run_id: null })}
          >
            <MessageSquarePlus size={15} />
            新建任务
          </button>
        </div>
      </div>
      <div className="stats-grid operations-stats">
        <OperationStat
          label="当前任务"
          value={String(rows.length).padStart(2, "0")}
          caption="最近更新的执行记录"
          icon={<SquareTerminal size={18} />}
        />
        <OperationStat
          label="执行中"
          value={String(active).padStart(2, "0")}
          caption="PENDING · RUNNING · WAITING_INPUT"
          icon={<Activity size={18} />}
          highlight={active > 0}
        />
        <OperationStat
          label="已完成"
          value={String(rows.filter((row) => row.status === "SUCCEEDED").length).padStart(2, "0")}
          caption="最近 50 条任务"
          icon={<CheckCircle2 size={18} />}
        />
        <OperationStat
          label="失败"
          value={String(failed).padStart(2, "0")}
          caption="需要关注的执行"
          icon={<XCircle size={18} />}
          alert={failed > 0}
        />
      </div>
      <section className="panel operations-panel">
        <div className="panel-title">
          <h2>
            执行记录 <span className="count-badge">{query.data ? rows.length : "—"}</span>
          </h2>
          <span className="muted">按最近更新时间倒序</span>
        </div>
        <form
          className="operations-filters"
          key={`${params.get("q") || ""}:${params.get("status") || ""}`}
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            navigate({
              view: "runs",
              q: String(data.get("q") || "").trim() || null,
              status: String(data.get("status") || "") || null,
              run_id: null,
            });
          }}
        >
          <label className="search-input">
            <Search size={16} />
            <input
              name="q"
              defaultValue={params.get("q") || ""}
              placeholder="搜索 Run ID、任务目标或摘要…"
              aria-label="搜索 Run"
            />
          </label>
          <select name="status" defaultValue={params.get("status") || ""} aria-label="Run 状态筛选">
            <option value="">全部状态</option>
            {Object.entries(runLabels).map(([key, label]) => (
              <option value={key} key={key}>
                {label}
              </option>
            ))}
          </select>
          <button className="button filter-submit" type="submit">
            <ListFilter size={14} />
            筛选
          </button>
        </form>
        {query.isPending ? (
          <Loading />
        ) : query.isError ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : !rows.length ? (
          <div className="empty-state">
            <Bot size={32} />
            <h3>还没有任务记录</h3>
            <p>提交一个任务后，它会出现在这里并实时更新。</p>
            <button className="button primary" onClick={() => navigate({ view: "new" })}>
              <MessageSquarePlus size={14} />
              新建任务
            </button>
          </div>
        ) : (
          <div className="run-list">
            {rows.map((row) => (
              <RunRow key={row.run_id} row={row} />
            ))}
          </div>
        )}
      </section>
      <div className="list-note">
        <Server size={14} />
        <span>任务状态来自 API 持久化记录；实时事件在任务详情页通过 SSE 接收。</span>
      </div>
    </main>
  );
}

function RunRow({ row }: { row: RunView }) {
  const label = row.input.replace(/\s+/g, " ").trim();
  return (
    <button className="run-row" onClick={() => navigate({ view: "run", run_id: row.run_id })}>
      <span className={`run-row-icon ${row.status.toLowerCase()}`}>
        {row.status === "RUNNING" ? <LoaderCircle size={17} className="spin" /> : <Bot size={17} />}
      </span>
      <span className="run-row-main">
        <strong>{label.slice(0, 110) || "未命名任务"}</strong>
        <span className="mono secondary">{row.run_id}</span>
      </span>
      <span className="run-row-summary">{row.summary || "等待执行结果…"}</span>
      <RunStatusPill value={row.status} />
      <span className="run-row-time">
        <Clock3 size={13} />
        {dateTime(row.updated_at)}
      </span>
      <ArrowUpRight size={15} className="muted" />
    </button>
  );
}

export function NewRunPage({ tenant }: { tenant: string }) {
  const queryClient = useQueryClient();
  const models = useModelOptions(tenant);
  const [input, setInput] = useState("");
  const [modelProfile, setModelProfile] = useState(() => storedModelProfile());
  const [reasoningEffort, setReasoningEffort] = useState(() => storedReasoningEffort());
  const [role, setRole] = useState("");
  const [constraints, setConstraints] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!input.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await createRun(tenant, {
        input: input.trim(),
        requested_role: role.trim() || undefined,
        constraints: constraints.trim() ? { instruction: constraints.trim() } : undefined,
        model_profile: usableModelProfile(modelProfile, models.data?.items),
        reasoning_effort: usableReasoningEffort(
          reasoningEffort,
          modelProfile,
          models.data?.items,
        ),
      });
      await queryClient.invalidateQueries({ queryKey: ["sessions", tenant] });
      navigate({
        view: "run",
        run_id: result.run_id,
        session_id: result.session_id,
        q: null,
        status: null,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务提交失败。");
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <main className="new-run-page">
      <button className="back-button" onClick={() => navigate({ view: "runs", run_id: null })}>
        <ArrowLeft size={14} />
        返回 Runs
      </button>
      <div className="new-run-intro">
        <div className="eyebrow">
          <span className="small-dot" />
          NEW AGENT RUN
        </div>
        <h1>你想让 Agent 完成什么？</h1>
        <p>写下目标和上下文，EnterpriseAgent 会规划、执行并记录每一步。</p>
      </div>
      <form className="composer" onSubmit={submit}>
        <ModelSelector
          options={models.data?.items || []}
          value={modelProfile}
          onChange={setModelProfile}
          effort={reasoningEffort}
          onEffortChange={setReasoningEffort}
          loading={models.isPending}
        />
        <label htmlFor="run-input">任务目标</label>
        <textarea
          id="run-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="例如：分析本周销售数据，找出异常趋势，并生成一份简明报告。"
          rows={8}
          autoFocus
          required
        />
        <div className="composer-footer">
          <button className="text-button" type="button" onClick={() => setAdvanced(!advanced)}>
            {advanced ? "收起高级参数" : "展开高级参数"}
          </button>
          <span className="composer-hint">Enter 提交 · Shift + Enter 换行</span>
          <button className="button primary" type="submit" disabled={!input.trim() || submitting}>
            {submitting ? <LoaderCircle size={14} className="spin" /> : <Send size={14} />}
            {submitting ? "提交中…" : "开始执行"}
          </button>
        </div>
        {advanced && (
          <div className="advanced-fields">
            <label>
              <span>请求角色</span>
              <input value={role} onChange={(event) => setRole(event.target.value)} placeholder="例如：researcher" />
            </label>
            <label>
              <span>执行约束</span>
              <textarea
                value={constraints}
                onChange={(event) => setConstraints(event.target.value)}
                placeholder="补充数据范围、输出格式或安全限制…"
                rows={3}
              />
            </label>
          </div>
        )}
      </form>
      {error && <div className="notice warning" role="alert">{error}</div>}
      <div className="new-run-notes">
        <div><SquareTerminal size={16} /><span>每次执行都会生成独立 Run，可在详情中实时观察。</span></div>
        <div><Activity size={16} /><span>任务可以暂停在 WAITING_INPUT，补充信息后继续。</span></div>
        <div><FileText size={16} /><span>生成的报告和文件会出现在 Artifacts 面板。</span></div>
      </div>
    </main>
  );
}

export function RunDetailPage({
  tenant,
  runId,
  sessionId,
}: {
  tenant: string;
  runId: string;
  sessionId?: string;
}) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [notice, setNotice] = useState("");
  const [input, setInput] = useState("");
  const [tab, setTab] = useState<"conversation" | "timeline" | "artifacts" | "json">("timeline");
  const [busy, setBusy] = useState("");
  const query = useQuery({
    queryKey: ["run", tenant, runId],
    queryFn: ({ signal }) => getRun(runId, tenant, signal),
    refetchInterval: (state) => (state.state.data && activeStatuses.includes(state.state.data.status) ? 2500 : false),
  });
  useEffect(() => {
    const row = query.data;
    if (!row) return;
    const resolvedSessionId = sessionId || row.session_id;
    if (resolvedSessionId && sessionId !== resolvedSessionId) {
      navigate({ session_id: resolvedSessionId }, true);
    }
  }, [query.data, runId, sessionId, tenant]);
  const artifacts = useQuery({
    queryKey: ["artifacts", tenant, runId],
    queryFn: ({ signal }) => getArtifacts(tenant, runId, signal),
    enabled: tab === "artifacts",
  });
  useEffect(() => {
    const controller = new AbortController();
    setEvents([]);
    void streamRunEvents(tenant, runId, 0, controller.signal, (event) => {
      setEvents((current) => current.some((item) => item.sequence === event.sequence) ? current : [...current, event]);
    }).then(() => {
      if (!controller.signal.aborted) void queryClient.invalidateQueries({ queryKey: ["run", tenant, runId] });
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setNotice(cause instanceof Error ? cause.message : "实时事件流暂时不可用，将继续自动刷新。");
    });
    return () => controller.abort();
  }, [queryClient, runId, tenant]);
  const row = query.data;
  const sortedEvents = useMemo(() => [...events].sort((a, b) => a.sequence - b.sequence), [events]);
  async function action(name: string, callback: () => Promise<unknown>) {
    setBusy(name);
    setNotice("");
    try {
      await callback();
      await query.refetch();
      setNotice(name === "cancel" ? "已发送取消请求。" : "操作已提交，任务将继续执行。");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "操作失败。");
    } finally {
      setBusy("");
    }
  }
  async function submitInput(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!input.trim()) return;
    const targetSessionId = sessionId || row?.session_id;
    await action("input", async () => {
      await addRunInput(tenant, runId, input.trim());
      await resumeRun(tenant, runId);
      if (targetSessionId) {
        await queryClient.invalidateQueries({ queryKey: ["session", tenant, targetSessionId] });
      }
      await queryClient.invalidateQueries({ queryKey: ["sessions", tenant] });
      setInput("");
    });
  }
  return (
    <main className="run-detail-page">
      <button className="back-button" onClick={() => navigate({ view: "runs", run_id: null })}>
        <ArrowLeft size={14} />
        返回 Runs
      </button>
      {query.isPending ? <Loading /> : query.isError || !row ? (
        <ErrorState error={query.error || new Error("未找到这个 Run。")} retry={() => void query.refetch()} />
      ) : (
        <>
          <div className="run-detail-heading">
            <div>
              <div className="eyebrow">RUN DETAIL</div>
              <h1>{row.input.replace(/\s+/g, " ").slice(0, 92) || "执行详情"}</h1>
              <div className="id-line"><span className="muted">RUN</span><code>{row.run_id}</code></div>
            </div>
            <div className="heading-actions">
              <RunStatusPill value={row.status} />
              {activeStatuses.includes(row.status) && (
                <button className="button danger-button" disabled={Boolean(busy)} onClick={() => void action("cancel", () => cancelRun(tenant, runId))}>
                  <PauseCircle size={14} />
                  {busy === "cancel" ? "取消中…" : "取消运行"}
                </button>
              )}
              <button className="button" onClick={() => void query.refetch()}><RefreshCw size={14} className={query.isFetching ? "spin" : ""} />刷新</button>
            </div>
          </div>
          {notice && <div className="notice" role="status">{notice}</div>}
          <div className="run-detail-grid">
            <section className="run-main-column">
              <div className="run-metrics">
                <div><span>状态</span><strong>{runLabels[row.status]}</strong></div>
                <div><span>路由</span><strong>{row.route || "待规划"}</strong></div>
                <div><span>模型</span><strong>{row.model_profile || "按配置默认"}</strong></div>
                <div><span>模型难度</span><strong>{reasoningEffortLabel(row.reasoning_effort)}</strong></div>
                <div><span>创建时间</span><strong>{dateTime(row.created_at)}</strong></div>
                <div><span>最后更新</span><strong>{dateTime(row.updated_at)}</strong></div>
              </div>
              {row.status === "WAITING_INPUT" && (
                <form className="input-request" onSubmit={submitInput}>
                  <div className="input-request-heading"><CircleDot size={17} /><div><strong>Agent 需要更多信息</strong><span>{row.missing_information.join("；") || "请补充必要上下文后继续。"}</span></div></div>
                  <div className="input-request-controls"><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="补充信息…" aria-label="补充 Run 信息" /><button className="button primary" disabled={!input.trim() || Boolean(busy)}><Send size={14} />{busy === "input" ? "提交中…" : "提交并继续"}</button></div>
                </form>
              )}
              <div className="run-tabs" role="tablist" aria-label="Run 内容">
                <button className={tab === "conversation" ? "active" : ""} onClick={() => setTab("conversation")} role="tab" aria-selected={tab === "conversation"}>
                  <MessageCircle size={13} /> 对话
                </button>
                <button className={tab === "timeline" ? "active" : ""} onClick={() => setTab("timeline")} role="tab" aria-selected={tab === "timeline"}>Events <span>{events.length}</span></button>
                <button className={tab === "artifacts" ? "active" : ""} onClick={() => setTab("artifacts")} role="tab" aria-selected={tab === "artifacts"}>Artifacts</button>
                <button className={tab === "json" ? "active" : ""} onClick={() => setTab("json")} role="tab" aria-selected={tab === "json"}>Raw JSON</button>
              </div>
              {tab === "conversation" && <ConversationPanel tenant={tenant} sessionId={sessionId || runId} />}
              {tab === "timeline" && <EventTimeline events={sortedEvents} />}
              {tab === "artifacts" && <ArtifactList state={artifacts} tenant={tenant} runId={runId} />}
              {tab === "json" && <pre className="raw-json">{JSON.stringify(row, null, 2)}</pre>}
            </section>
            <aside className="run-context" aria-label="Run 上下文">
              <div className="context-card">
                <div className="context-title"><span>执行上下文</span><Bot size={16} /></div>
                <dl>
                  <div><dt>租户</dt><dd>{row.tenant_id}</dd></div>
                  <div><dt>Run ID</dt><dd className="mono">{row.run_id}</dd></div>
                  <div><dt>Budget</dt><dd>{Object.keys(row.budget).length ? JSON.stringify(row.budget) : "未设置"}</dd></div>
                </dl>
              </div>
              <div className="context-card">
                <div className="context-title"><span>快捷入口</span><ArrowUpRight size={16} /></div>
                <button className="context-action" onClick={() => navigate({ view: null, run_id: runId })}><Activity size={15} /><span><strong>Trace Explorer</strong><small>查看调用树和 Waterfall</small></span><ArrowUpRight size={14} /></button>
                <button className="context-action" onClick={() => setTab("artifacts")}><FileText size={15} /><span><strong>Artifacts</strong><small>查看任务生成的文件</small></span><ArrowUpRight size={14} /></button>
              </div>
              <div className="context-card context-tip"><Play size={15} /><p>实时事件通过 SSE 连接接收。任务进入终态后会停止自动刷新。</p></div>
            </aside>
          </div>
        </>
      )}
    </main>
  );
}

function ConversationPanel({ tenant, sessionId }: { tenant: string; sessionId: string }) {
  const queryClient = useQueryClient();
  const models = useModelOptions(tenant);
  const query = useQuery({
    queryKey: ["session", tenant, sessionId],
    queryFn: ({ signal }) => getSession(tenant, sessionId, signal),
    refetchInterval: 3000,
  });
  const session = query.data;
  const [input, setInput] = useState("");
  const [modelProfile, setModelProfile] = useState(() => storedModelProfile());
  const [reasoningEffort, setReasoningEffort] = useState(() => storedReasoningEffort());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !input.trim()) return;
    setSending(true);
    setError("");
    const message = input.trim();
    try {
      const result = await createRun(tenant, {
        input: message,
        message,
        session_id: session.session_id,
        model_profile: usableModelProfile(modelProfile, models.data?.items),
        reasoning_effort: usableReasoningEffort(
          reasoningEffort,
          modelProfile,
          models.data?.items,
        ),
      });
      await queryClient.invalidateQueries({ queryKey: ["sessions", tenant] });
      navigate({ view: "run", run_id: result.run_id, session_id: result.session_id });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "继续对话失败。");
    } finally {
      setSending(false);
    }
  }

  if (query.isPending) {
    return <Loading />;
  }
  if (query.isError || !session) {
    return (
      <div className="conversation-empty">
        <p>无法加载这段聊天记录。</p>
        <button className="button" onClick={() => void query.refetch()}>
          <RefreshCw size={14} />重试
        </button>
      </div>
    );
  }
  return (
    <div className="conversation-panel">
      <div className="conversation-messages">
        {session.messages.map((message) => (
          <div className={`conversation-message ${message.role}`} key={message.message_id}>
            <span className="conversation-avatar">{message.role === "user" ? "你" : "EA"}</span>
            <div>
              <div className="conversation-message-meta">
                <strong>{message.role === "user" ? "你" : "EnterpriseAgent"}</strong>
                <time>{dateTime(message.created_at)}</time>
              </div>
              <p>{message.content}</p>
            </div>
          </div>
        ))}
      </div>
      <form className="conversation-composer" onSubmit={submit}>
        <ModelSelector
          options={models.data?.items || []}
          value={modelProfile}
          onChange={setModelProfile}
          effort={reasoningEffort}
          onEffortChange={setReasoningEffort}
          loading={models.isPending}
        />
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="继续这个对话…"
          rows={3}
          aria-label="继续对话"
        />
        <div>
          {error && <span className="conversation-error">{error}</span>}
          <button className="button primary" disabled={!input.trim() || sending}>
            {sending ? <LoaderCircle size={14} className="spin" /> : <Send size={14} />}
            {sending ? "提交中…" : "发送"}
          </button>
        </div>
      </form>
    </div>
  );
}

const MODEL_STORAGE_KEY = "enterprise-agent.selected-model-profile";
const EFFORT_STORAGE_KEY = "enterprise-agent.selected-reasoning-effort";

const reasoningEffortLabels: Record<ReasoningEffort, string> = {
  minimal: "极低",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "超高",
  max: "最高",
};

function storedModelProfile(): string {
  try {
    return window.localStorage.getItem(MODEL_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function storedReasoningEffort(): ReasoningEffort | "" {
  try {
    const value = window.localStorage.getItem(EFFORT_STORAGE_KEY) || "";
    return value in reasoningEffortLabels ? (value as ReasoningEffort) : "";
  } catch {
    return "";
  }
}

function usableModelProfile(value: string, options?: ModelOption[]): string | undefined {
  if (!value || !options) return value || undefined;
  const option = options.find((item) => item.profile === value);
  return option?.available && option.status !== "sunset" ? value : undefined;
}

function usableReasoningEffort(
  value: ReasoningEffort | "",
  profile: string,
  options?: ModelOption[],
): ReasoningEffort | undefined {
  if (!value || !options) return value || undefined;
  const selected = options.find((item) => item.profile === profile)
    || options.find((item) => item.default);
  return selected?.reasoning_efforts?.includes(value) ? value : undefined;
}

function reasoningEffortLabel(value: ReasoningEffort | null): string {
  return value ? reasoningEffortLabels[value] || value : "自动";
}

function useModelOptions(tenant: string) {
  return useQuery({
    queryKey: ["models", tenant],
    queryFn: ({ signal }) => getModels(tenant, signal),
    staleTime: 60000,
  });
}

function ModelSelector({
  options,
  value,
  onChange,
  effort,
  onEffortChange,
  loading,
}: {
  options: ModelOption[];
  value: string;
  onChange: (value: string) => void;
  effort: ReasoningEffort | "";
  onEffortChange: (value: ReasoningEffort | "") => void;
  loading: boolean;
}) {
  const available = options.filter((option) => option.available && option.status !== "sunset");
  const selected = available.some((option) => option.profile === value) ? value : "";
  const selectedOption = available.find((option) => option.profile === selected)
    || available.find((option) => option.default);
  const effortOptions = selectedOption?.reasoning_efforts || [];
  const selectedEffort = effortOptions.includes(effort as ReasoningEffort) ? effort : "";

  function select(value: string) {
    onChange(value);
    const nextOption = available.find((option) => option.profile === value)
      || available.find((option) => option.default);
    if (!nextOption?.reasoning_efforts?.includes(effort as ReasoningEffort)) {
      selectEffort("");
    }
    try {
      if (value) window.localStorage.setItem(MODEL_STORAGE_KEY, value);
      else window.localStorage.removeItem(MODEL_STORAGE_KEY);
    } catch {
      // Local storage is only a convenience; model selection still works per turn.
    }
  }

  function selectEffort(value: ReasoningEffort | "") {
    onEffortChange(value);
    try {
      if (value) window.localStorage.setItem(EFFORT_STORAGE_KEY, value);
      else window.localStorage.removeItem(EFFORT_STORAGE_KEY);
    } catch {
      // Local storage is only a convenience; effort selection still works per turn.
    }
  }

  return (
    <label className="model-selector">
      <span className="model-selector-label">模型</span>
      <select
        aria-label="选择模型"
        value={selected}
        onChange={(event) => select(event.target.value)}
        disabled={loading && options.length === 0}
      >
        <option value="">
          {loading ? "加载模型配置…" : `使用默认模型${options.find((option) => option.default)?.label ? ` · ${options.find((option) => option.default)?.label}` : ""}`}
        </option>
        {options.map((option) => (
          <option
            key={option.profile}
            value={option.profile}
            disabled={!option.available || option.status === "sunset"}
          >
            {option.label} · {option.provider}
            {!option.available ? "（离线模式不可用）" : option.status === "deprecated" ? "（即将弃用）" : ""}
          </option>
        ))}
      </select>
      <span className="model-selector-divider" />
      <span className="model-selector-label">难度</span>
      <select
        aria-label="选择模型难度"
        value={selectedEffort}
        onChange={(event) => selectEffort(event.target.value as ReasoningEffort | "")}
        disabled={loading || effortOptions.length === 0}
      >
        <option value="">自动</option>
        {effortOptions.map((item) => (
          <option key={item} value={item}>
            {reasoningEffortLabels[item] || item}
          </option>
        ))}
      </select>
      {available.length > 0 && (
        <span className="model-selector-hint">
          {effortOptions.length > 0 ? "本次对话可单独调整推理强度" : "该模型不支持难度调整"}
        </span>
      )}
    </label>
  );
}

function EventTimeline({ events }: { events: RunEvent[] }) {
  if (!events.length) {
    return <div className="timeline-empty"><LoaderCircle size={22} className="spin" /><p>等待 Worker 发回执行事件…</p></div>;
  }
  return <div className="event-timeline">{events.map((event) => <div className="event-item" key={event.sequence}><span className={`event-icon ${event.event_type.includes("failed") || event.event_type.includes("cancelled") ? "error" : event.event_type.includes("completed") ? "success" : ""}`}><EventIcon type={event.event_type} /></span><div className="event-body"><div className="event-meta"><strong>{event.stage}</strong><span className="mono">#{event.sequence}</span><time>{dateTime(event.created_at)}</time></div><p>{event.message}</p>{Object.keys(event.data).length > 0 && <details><summary>查看事件数据</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>}</div></div>)}</div>;
}

function EventIcon({ type }: { type: string }) {
  if (type.includes("completed")) return <CheckCircle2 size={14} />;
  if (type.includes("failed")) return <XCircle size={14} />;
  if (type.includes("input")) return <MessageSquarePlus size={14} />;
  if (type.includes("tool")) return <SquareTerminal size={14} />;
  return <Activity size={14} />;
}

function ArtifactList({ state, tenant, runId }: { state: ReturnType<typeof useQuery<{ artifacts: Artifact[] }>>; tenant: string; runId: string }) {
  if (state.isPending) return <Loading />;
  if (state.isError) return <ErrorState error={state.error} retry={() => void state.refetch()} />;
  const rows = state.data?.artifacts || [];
  if (!rows.length) return <div className="timeline-empty"><FileText size={24} /><p>这个 Run 还没有生成文件。</p></div>;
  return <div className="artifact-list">{rows.map((artifact) => <div className="artifact-row" key={artifact.artifact_id}><span className="artifact-icon"><FileText size={17} /></span><span><strong>{artifact.name}</strong><small>{artifact.media_type} · {formatBytes(artifact.size_bytes)} · {dateTime(artifact.created_at)}</small></span><button className="icon-button bordered" aria-label={`下载 ${artifact.name}`} onClick={() => void downloadArtifact(tenant, runId, artifact.artifact_id, artifact.name)}><ArrowDownToLine size={15} /></button></div>)}</div>;
}

function RunStatusPill({ value }: { value: RunStatus }) {
  return <span className={`run-status-pill ${value.toLowerCase()}`}><span className="status-dot" />{runLabels[value]}</span>;
}

function OperationStat({ label, value, caption, icon, alert = false, highlight = false }: { label: string; value: string; caption: string; icon: React.ReactNode; alert?: boolean; highlight?: boolean }) {
  return <div className={`stat-card ${alert ? "alert-stat" : ""} ${highlight ? "active-stat" : ""}`}><div className="stat-label">{label}{icon}</div><div className="stat-value">{value}</div><div className="stat-caption">{caption}</div></div>;
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
