import type {
  Artifact,
  RunEvent,
  RunView,
  ReasoningEffort,
  SessionSummary,
  SessionView,
  ModelOption,
  TraceList,
  TraceView,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request(path: string, tenant: string, signal?: AbortSignal) {
  const response = await fetch(`/api/v1${path}`, {
    headers: { "X-Tenant-Id": tenant },
    signal,
  });
  if (!response.ok) {
    const message =
      response.status === 404
        ? "未找到记录，或当前租户无权访问。"
        : response.status === 503
          ? "API 暂不可用。请检查服务状态及身份适配器配置。"
          : response.status === 422
            ? "查询参数无效，请检查时间范围或重置筛选。"
            : response.status === 409
              ? "当前模型不可用，请重新选择模型。"
            : `请求失败（HTTP ${response.status}），请检查 API 服务。`;
    throw new ApiError(response.status, message);
  }
  return response;
}

async function jsonRequest(
  path: string,
  tenant: string,
  init: RequestInit = {},
  signal?: AbortSignal,
) {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "X-Tenant-Id": tenant,
      ...(init.headers || {}),
    },
    signal,
  });
  if (!response.ok) {
    const message =
      response.status === 404
        ? "未找到记录，或当前租户无权访问。"
        : response.status === 422
          ? "请求参数无效，请检查输入。"
            : response.status === 409
              ? "当前模型不可用，请重新选择模型。"
          : `请求失败（HTTP ${response.status}），请检查 API 服务。`;
    throw new ApiError(response.status, message);
  }
  return response;
}

export async function getRuns(
  params: URLSearchParams,
  tenant: string,
  signal: AbortSignal,
): Promise<{ items: RunView[] }> {
  const query = new URLSearchParams();
  for (const key of ["q", "status"]) {
    if (params.get(key)) query.set(key, params.get(key)!);
  }
  query.set("limit", "50");
  return (await request(`/runs?${query}`, tenant, signal)).json();
}

export async function getRun(
  runId: string,
  tenant: string,
  signal?: AbortSignal,
): Promise<RunView> {
  return (
    await request(`/runs/${encodeURIComponent(runId)}`, tenant, signal)
  ).json();
}

export async function createRun(
  tenant: string,
  body: {
    input: string;
    constraints?: Record<string, string>;
    requested_role?: string;
    idempotency_key?: string;
    session_id?: string;
    message?: string;
    model_profile?: string;
    reasoning_effort?: ReasoningEffort;
  },
): Promise<{
  run_id: string;
  session_id: string;
  status: string;
  model_profile: string | null;
  reasoning_effort: ReasoningEffort | null;
}> {
  return (
    await jsonRequest(`/runs`, tenant, {
      method: "POST",
      body: JSON.stringify(body),
    })
  ).json();
}

export async function getModels(
  tenant: string,
  signal?: AbortSignal,
): Promise<{ items: ModelOption[]; offline_mode: boolean }> {
  return (await request("/models", tenant, signal)).json();
}

export async function getSessions(
  tenant: string,
  signal?: AbortSignal,
): Promise<{ items: SessionSummary[] }> {
  return (await request("/sessions?limit=100", tenant, signal)).json();
}

export async function getSession(
  tenant: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionView> {
  return (
    await request(`/sessions/${encodeURIComponent(sessionId)}`, tenant, signal)
  ).json();
}

export async function cancelRun(tenant: string, runId: string) {
  return (
    await jsonRequest(`/runs/${encodeURIComponent(runId)}/cancel`, tenant, {
      method: "POST",
    })
  ).json();
}

export async function resumeRun(tenant: string, runId: string) {
  return (
    await jsonRequest(`/runs/${encodeURIComponent(runId)}/resume`, tenant, {
      method: "POST",
    })
  ).json();
}

export async function addRunInput(
  tenant: string,
  runId: string,
  input: string,
) {
  return (
    await jsonRequest(`/runs/${encodeURIComponent(runId)}/input`, tenant, {
      method: "POST",
      body: JSON.stringify({ input }),
    })
  ).json();
}

export async function getArtifacts(
  tenant: string,
  runId: string,
  signal?: AbortSignal,
): Promise<{ artifacts: Artifact[] }> {
  return (
    await request(`/runs/${encodeURIComponent(runId)}/artifacts`, tenant, signal)
  ).json();
}

export async function downloadArtifact(
  tenant: string,
  runId: string,
  artifactId: string,
  name: string,
) {
  const response = await request(
    `/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/download`,
    tenant,
  );
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function streamRunEvents(
  tenant: string,
  runId: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
) {
  const response = await fetch(
    `/api/v1/runs/${encodeURIComponent(runId)}/events?after=${after}`,
    { headers: { Accept: "text/event-stream", "X-Tenant-Id": tenant }, signal },
  );
  if (!response.ok || !response.body) {
    throw new ApiError(response.status || 500, "实时事件流暂时不可用。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const data = block
        .split("\n")
        .find((line) => line.startsWith("data: "))
        ?.slice(6);
      if (data) onEvent(JSON.parse(data) as RunEvent);
    }
  }
}

export async function getTraces(
  params: URLSearchParams,
  tenant: string,
  signal: AbortSignal,
): Promise<TraceList> {
  const query = new URLSearchParams();
  for (const key of [
    "q",
    "status",
    "name",
    "tag",
    "has_error",
    "start_time",
    "end_time",
    "cursor",
  ]) {
    if (params.get(key)) query.set(key, params.get(key)!);
  }
  query.set("limit", "30");
  return (await request(`/traces?${query}`, tenant, signal)).json();
}
export async function getTrace(
  runId: string,
  tenant: string,
  signal: AbortSignal,
): Promise<TraceView> {
  return (
    await request(`/runs/${encodeURIComponent(runId)}/trace`, tenant, signal)
  ).json();
}
export async function exportTrace(runId: string, tenant: string) {
  const blob = await (
    await request(`/runs/${encodeURIComponent(runId)}/trace/export`, tenant)
  ).blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `trace-${runId}.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
