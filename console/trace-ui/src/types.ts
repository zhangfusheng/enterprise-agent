export type TraceStatus =
  "success" | "error" | "running" | "interrupted" | "incomplete";
export type RunStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING_INPUT"
  | "SUCCEEDED"
  | "PARTIAL"
  | "FAILED"
  | "CANCELLED";
export type RunType =
  "chain" | "llm" | "tool" | "retriever" | "embedding" | "prompt" | "parser";
export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export interface Span {
  id: string;
  trace_id: string;
  parent_run_id: string | null;
  name: string;
  run_type: RunType;
  start_time: string;
  end_time: string | null;
  inputs: Record<string, Json>;
  outputs: Record<string, Json>;
  error: string | null;
  extra: Record<string, Json>;
  tags: string[];
  dotted_order: string;
  children: Span[];
  depth: number;
  has_error: boolean;
  status: TraceStatus;
  duration_ms: number | null;
  timing_source: "measured" | "reconstructed";
}
export interface TraceSummary {
  run_id: string;
  trace_id: string;
  root_name: string;
  status: TraceStatus;
  run_status: RunStatus | null;
  start_time: string;
  end_time: string | null;
  duration_ms: number | null;
  span_count: number;
  error_count: number;
  has_error: boolean;
  tags: string[];
  route: string | null;
}
export interface TraceList {
  items: TraceSummary[];
  next_cursor: string | null;
}
export interface TraceView {
  run_id: string;
  run_status: RunStatus | null;
  runs: Span[];
  roots: Span[];
}
export interface RunView {
  run_id: string;
  session_id?: string | null;
  tenant_id: string;
  status: RunStatus;
  input: string;
  model_profile: string | null;
  reasoning_effort: ReasoningEffort | null;
  route: string | null;
  summary: string | null;
  missing_information: string[];
  created_at: string;
  updated_at: string;
  budget: Record<string, Json>;
}
export interface ModelOption {
  profile: string;
  label: string;
  provider: string;
  model_name: string;
  status: "active" | "deprecated" | "sunset";
  capabilities: string[];
  reasoning_efforts: ReasoningEffort[];
  available: boolean;
  default: boolean;
  unavailable_reason: string | null;
}
export type ReasoningEffort =
  | "minimal"
  | "low"
  | "medium"
  | "high"
  | "xhigh"
  | "max";
export type SessionMessageRole = "user" | "assistant";
export interface SessionMessage {
  message_id: string;
  role: SessionMessageRole;
  content: string;
  created_at: string;
  run_id: string;
}
export interface SessionSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  latest_run_id: string | null;
}
export interface SessionView extends SessionSummary {
  messages: SessionMessage[];
}
export interface RunEvent {
  sequence: number;
  event_type: string;
  stage: string;
  message: string;
  created_at: string;
  data: Record<string, Json>;
}
export interface Artifact {
  artifact_id: string;
  run_id: string;
  tenant_id: string;
  name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  path: string;
  created_at: string;
  metadata: Record<string, Json>;
}
