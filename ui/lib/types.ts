export type Verdict = "keep" | "reject" | "review";
export type VerdictCounts = Partial<Record<Verdict, number>>;
export type Role = "judge" | "team";

export interface EventSummary {
  event_id: string;
  index: number;
  label: string;
  name?: string;
  category?: string;
  event_date?: string;
  n_photos?: number;
  n_with_original?: number;
  n_cameras?: number;
  verdict_counts?: VerdictCounts;
  latest_run?: { run_id: string; status: string; flavor?: string; started_at?: string };
}

export interface Run {
  run_id: string;
  event_id: string;
  event_label?: string;
  status: string;
  flavor?: string;
  started_at?: string;
  ended_at?: string;
  updated_at?: string;
  opencv_version?: string;
  /** HAL cv2 loaded on the worker: "carotene" | "kleidicv-26.03+carotene". */
  opencv_hal?: string;
  cv2_file?: string;
  instance_type?: string;
  thresholds_sha?: string;
  verdict_counts?: VerdictCounts;
  tool_call_count?: number;
  escalation_count?: number;
  cost_estimate_usd?: number;
  photos_processed?: number;
  wall_s?: number;
  /** API-computed; present only for a finished run with consistent stamps. */
  duration_s?: number;
  /** API-computed from worker_escalations / agent_escalations (old runs: escalation_count). */
  escalations?: { worker?: number; agent?: number };
  agent_photos?: number;
  /** As stored, e.g. deployed | local_calibration | local | agentcore; the API fills "deployed" for
   *  run_aws_* / run_infra_* ids. */
  run_kind?: string;
  /** Where the agent pass ran, stamped by the agent: agentcore | fargate | local. */
  agent_host?: string;
  /** Who gave the latest approval answer (API, from RUN# resume_decisions) and the verbatim labeller. */
  last_answer_source?: DecisionSource;
  last_labeller?: string;
  run_note?: string;
  paused_at?: string;
  worker_ended_at?: string;
  llm_provider?: string;
  /** How approval interrupts were answered on this run, e.g. "scripted". */
  approvals_source?: string;
  planner_model?: string;
  reasons_model?: string;
  error?: string;
  event?: EventSummary;
}

export type DecisionSource = "agent" | "scripted" | "lead_test" | "unknown" | "ui" | "file" | "http";

export interface Member {
  photo_id: string;
  proxy_url: string;
  verdict?: Verdict;
  reason_codes?: string[];
  reason_text?: string;
  confidence?: number;
  decided_by?: "agent" | "human" | "worker" | "scripted" | "lead_test";
  /** Who made the verdict: the agent; a person via the review desk (ui), a decisions file or HTTP resume;
   *  a scripted auto-approver (no person looked); or unknown (non-agent but unstamped, never shown as a
   *  person). For non-agent verdicts `confidence` is the agent's own proposal confidence, if known. */
  decision_source?: DecisionSource;
  /** Verbatim labeller on the answer, if one was given ("lead-mechanism-test" is always lead_test). */
  labeller?: string;
  human_note?: string;
  scores?: Record<string, unknown>;
  is_best: boolean;
  rank?: number;
  score?: number;
  breakdown?: Record<string, number>;
}

export interface Group {
  group_id: string;
  /** Short human label from the API ("Group 12"); the full id goes in a title attribute. */
  group_label?: string;
  size: number;
  span_s?: number;
  best_photo_id?: string;
  margin?: number;
  tie?: boolean;
  run_id?: string;
  members: Member[];
  verdict_counts: VerdictCounts;
  reason_counts: Record<string, number>;
  pending_approval: boolean;
  contact_sheet_url: string;
}

export interface GroupsPage {
  event: EventSummary;
  source?: "dynamodb" | "s3";
  items: Group[];
  next_cursor: string | null;
  event_verdict_counts?: VerdictCounts;
}

export interface TraceLine {
  run_id: string;
  seq: number;
  ts?: string;
  type: "tool_call" | "tool_result" | "model_reasoning" | "interrupt_resumed" | string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  text?: string;
  model?: string;
  opencv?: string;
  cv2_file?: string;
  latency_ms?: number;
  decisions?: unknown[];
  contact_sheet_urls?: string[];
  /** On interrupt_resumed / request_human_approval results: who supplied the answers. */
  answered_by?: DecisionSource;
  via_seqs?: number[];
}

export interface Pivot {
  kind: "sharpness_rescore" | "eyes_compare_in_group" | "small_margin_to_human" | "tagged" | string;
  policy: string;
  summary: string;
  /** Helper/scoring calls between the result and its consequence (e.g. make_contact_sheet). */
  via_seqs?: number[];
  result_seq: number;
  reasoning_seqs: number[];
  call_seq: number;
}

export interface TracePage {
  run_id: string;
  group_id?: string;
  source: string;
  items: TraceLine[];
  pivots: Pivot[];
  next_after: number | null;
}

export interface Proposed {
  keep?: string | string[];
  reject?: string[];
  review?: string[];
}

export interface Approval {
  group_label?: string;
  approval_id: string;
  run_id: string;
  event_id: string;
  group_id?: string;
  photo_id?: string;
  proposed?: Proposed | Verdict;
  reason?: string;
  reason_codes?: string[];
  confidence?: number;
  created_at?: string;
  resume_error?: string;
  contact_sheet_url?: string;
  members?: { photo_id: string; proxy_url: string }[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/** Published shape of bench/results/latest/results.json, as served by GET /bench. */
export interface BenchStage {
  name: string;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface BenchHost {
  label: string;
  instance_type?: string;
  opencv_version?: string;
  cv2_file?: string;
  images_per_sec?: number;
  cost_per_1k_usd?: number;
  cpu_util_mean?: number;
  peak_rss_mb?: number;
  n_images?: number;
  workers?: number;
  /** HAL actually loaded by cv2 (build information): "carotene" | "kleidicv-26.03+carotene". */
  opencv_hal?: string;
  custom_hal?: string;
  /** e.g. "/usr/bin/gcc14-g++ (ver 14.2.1)", when the benchmark publishes it. */
  cxx_compiler?: string;
  stages: BenchStage[];
}

export interface BenchDoc {
  generated_at?: string;
  synthetic?: boolean;
  n_images?: number;
  hosts: BenchHost[];
}

/** GET /runs/{id}/agent: the API's configured resume path and, for AgentCore, the runtime's live state. */
export interface RunAgent {
  run_id: string;
  path: "agentcore" | "http" | "none";
  run_status?: string;
  agent_host?: string;
  session_id?: string;
  live: { status: string; checked_at?: string; error?: string; note?: string; pending_approvals?: number } | null;
  live_skipped?: string;
}
