// Typed client for the LifeOS backend (FastAPI on 127.0.0.1:8765).

const BASE: string =
  import.meta.env.VITE_API_URL ??
  (typeof window !== "undefined" && window.location.port === "8765" ? "" : "http://127.0.0.1:8765");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: { "Content-Type": "application/json", "X-LifeOS-Client": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the LifeOS backend. Is it running on port 8765?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : res.statusText);
  return data as T;
}

export interface GpuStatus {
  models: { name: string; size: number; vram: number; until: string | null }[];
  vram: number;
  media_busy: boolean;
}
export interface OldData { found: string | null; imported: boolean; pending: boolean }
export interface UpdateConfig { repo: string; has_token: boolean }

const get = <T>(p: string) => request<T>("GET", p);

/** Share one request between callers: in flight, or answered in the last `ttl` ms (several views poll the same thing). */
function shared<T>(load: () => Promise<T>, ttl = 300): () => Promise<T> {
  let p: Promise<T> | null = null, done = 0, pending = false;
  return () => {
    if (p && (pending || Date.now() - done < ttl)) return p;
    pending = true;
    const cur = load().finally(() => { pending = false; done = Date.now(); });
    cur.catch(() => { if (p === cur) p = null; });
    p = cur;
    return cur;
  };
}
const post = <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const del = <T>(p: string) => request<T>("DELETE", p);
const qs = (o: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

// ---------------------------------------------------------------- types

export interface FileItem {
  id: number;
  path: string;
  name: string;
  ext: string;
  size: number;
  modified_at: string;
  category?: string | null;
  snippet?: string;
  folder_id?: number;
}

export interface Move {
  src: string;
  dest: string;
  category: string;
  size?: number;
  important?: boolean;
}

export interface Action {
  id: number;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  preview: { moves?: Move[]; by_category?: Record<string, number>; folder?: string; [k: string]: unknown } | null;
  status: "pending" | "running" | "executed" | "rejected" | "superseded" | "failed" | "undone" | "undoing";
  result: { summary?: string; moved?: { src: string; dest: string }[]; skipped?: unknown[] } | null;
  error: string | null;
  conversation_id: number | null;
  mission_id?: number | null;
  created_at: string;
  executed_at: string | null;
}

export interface Task {
  id: number;
  title: string;
  notes: string | null;
  project: string | null;
  priority: number;
  status: "open" | "done";
  due_at: string | null;
  remind_at: string | null;
  reminded_at: string | null;
  estimate_minutes: number | null;
  created_at: string;
  completed_at: string | null;
  score?: number;
}

export interface Memory {
  id: number;
  kind: string;
  content: string;
  source: string | null;
  pinned: number;
  created_at: string;
  updated_at: string;
  score?: number;
}

export interface StepResult {
  tool: string;
  args: Record<string, unknown>;
  status: "done" | "pending" | "error" | "blocked" | "executed" | "failed";
  summary: string;
  result?: Record<string, any>;
  action?: Action;
}

export interface Message {
  id: number;
  conversation_id: number;
  role: "user" | "assistant";
  content: string;
  data: { planner?: string; steps?: StepResult[]; pending_action_ids?: number[] } | null;
  created_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ActivityEntry {
  id: number;
  kind: string;
  tool: string | null;
  summary: string;
  details: unknown;
  status: string;
  action_id: number | null;
  created_at: string;
}

export interface Suggestion {
  id: string;
  kind: string;
  priority: number;
  title: string;
  detail: string;
  command?: string;
  link?: string;
  task_id?: number;
}

export interface Folder {
  id: number;
  path: string;
  label: string | null;
  created_at: string;
  last_indexed_at: string | null;
  file_count: number;
}

export interface LlmStatus {
  ok: boolean;
  models: string[];
  url: string;
}

export interface Dashboard {
  greeting: string;
  summary: { text: string; due_today: Task[]; overdue: Task[]; upcoming: Task[]; completed_today: number; pending_actions: number };
  priorities: Task[];
  suggestions: Suggestion[];
  pending_actions: Action[];
  recent_files: FileItem[];
  recent_activity: ActivityEntry[];
  counts: { files: number; memories: number; open_tasks: number; folders: number };
  automation_paused: boolean;
  llm: LlmStatus;
  crew: { agents: Pick<AgentStatus, "slug" | "name" | "role" | "color" | "icon" | "state" | "current">[]; active_missions: number; open_briefs: number; spend: Spend };
  automations: { enabled: number; total: number; next: { id: number; name: string; next_run_at: string }[]; recent_runs: WorkflowRun[] };
  events_today: CalEvent[];
}

export interface Settings {
  ollama_url: string;
  auto_start_ollama: boolean;
  ollama_timeout_s: number;
  ollama_one_model: boolean;
  ollama_keep_alive: string;
  ollama_num_ctx: number;
  chat_model: string;
  embed_model: string;
  planner: "auto" | "llm" | "rules";
  automation_paused: boolean;
  proactive_level: "off" | "low" | "normal";
  large_file_mb: number;
  stale_days: number;
  downloads_clutter_threshold: number;
  tool_policies: Record<string, "auto" | "confirm" | "deny">;
  theme_accent: string;
  user_name: string;
  dismissed_suggestions: string[];
  max_parallel_agents: number;
  mission_token_budget: number;
  agent_max_steps: number;
  agent_auto_extend: boolean;
  cloud_enabled: boolean;
  cloud_daily_budget_usd: number;
  model_prices: Record<string, { in: number; out: number }>;
  custom_provider_url: string;
  web_access: boolean;
  peer_review: boolean;
  reflection: boolean;
  auto_route: boolean;
  lessons_per_task: number;
  notification_prefs: { reminders: boolean; workflows: boolean; missions: boolean; suggestions: boolean };
  quiet_hours: { enabled: boolean; start: string; end: string };
  email: { host: string; port: number; username: string; folder: string };
}

export interface ToolInfo {
  name: string;
  description: string;
  risk: "read" | "write" | "destructive";
  default_policy: string;
  policy: string;
  parameters?: { properties?: Record<string, JsonProp>; required?: string[] };
}

export interface JsonProp {
  type?: string;
  anyOf?: JsonProp[];
  enum?: string[];
  items?: JsonProp;
  description?: string;
  default?: unknown;
  title?: string;
}

export interface Notification {
  id: number;
  title: string;
  body: string | null;
  task_id: number | null;
  kind?: string;
  read: number;
  created_at: string;
}

// ---------------------------------------------------------------- crew

export interface Agent {
  id: number;
  slug: string;
  name: string;
  role: string;
  description: string;
  persona: string;
  model: string;
  effective_model: string;
  tools: string[];
  color: string;
  icon: string;
  max_steps: number;
  enabled: boolean;
  builtin: boolean;
  candidates: string[];
  wanted_model: string;
  model_missing: boolean;
  recommended: { model: string; size: string; why: string }[];
  voice: string;
  quirks: string;
  catchphrases: string[];
  idle_lines: string[];
}

export type AgentState = "idle" | "working" | "waiting" | "asking" | "offline";

export interface MissionEvent {
  id: number;
  mission_id: number;
  task_id: number | null;
  agent: string | null;
  kind: string;
  summary: string;
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface AgentStatus extends Agent {
  state: AgentState;
  current: { mission_id: number; task_id: number | null; title: string; goal: string; since: string | null; status: string } | null;
  queue: number;
  tokens_today: number;
  cost_today: number;
  calls_today: number;
  last_event: MissionEvent | null;
}

export interface MissionTask {
  id: number;
  mission_id: number;
  key: string;
  agent: string;
  title: string;
  instructions: string;
  depends_on: string[];
  preset: unknown[] | null;
  status: "pending" | "running" | "waiting_approval" | "waiting_input" | "done" | "failed" | "skipped" | "cancelled";
  result: string | null;
  steps: number;
  started_at: string | null;
  finished_at: string | null;
  model: string | null;
  score: number | null;
  review: { score: number; feedback: string; lesson?: string; reviewer: string } | null;
  user_rating: number | null;
}

export interface Lesson {
  id: number;
  agent: string;
  text: string;
  source: "review" | "reflection" | "failure" | "rejection" | "user";
  mission_id: number | null;
  task_id: number | null;
  pinned: boolean;
  uses: number;
  created_at: string;
  updated_at: string;
}

export interface ScoreStats {
  tasks: number;
  done: number;
  avg_score: number | null;
  success_rate: number | null;
  avg_steps: number | null;
  thumbs_up: number;
  thumbs_down: number;
}

export interface ScoreCard extends ScoreStats {
  slug: string;
  name: string;
  color: string;
  icon: string;
  lessons: number;
  models: (ScoreStats & { model: string })[];
}

export interface LearningOverview {
  lessons: number;
  reviewed_tasks: number;
  exportable: number;
  settings: { peer_review: boolean; reflection: boolean; auto_route: boolean; lessons_per_task: number };
}

export interface MediaItem {
  id: number;
  kind: "image" | "video";
  prompt: string;
  backend: string;
  model: string | null;
  status: "queued" | "running" | "done" | "failed";
  path: string | null;
  name: string | null;
  params: { size?: string; width?: number; height?: number; seed?: number; frames?: number; negative?: string } | null;
  error: string | null;
  seconds: number | null;
  mission_id: number | null;
  agent: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface MediaSettings {
  backend: "comfyui" | "a1111" | "openai" | "off";
  comfy_url: string;
  a1111_url: string;
  image_checkpoint: string;
  video_checkpoint: string;
  video_text_encoder: string;
  image_workflow: string;
  video_workflow: string;
  output_folder: string;
  cloud_image_model: string;
  cloud_price_usd: number;
  free_vram: boolean;
  comfy_launcher: string;
  comfy_autostart: boolean;
}

export interface MediaStatus {
  backend: string;
  ok: boolean;
  checkpoints: string[];
  video: boolean;
  detail: string;
  launcher?: string | null;
  settings: MediaSettings;
}

export interface Services {
  ollama: { up: boolean; started_by_lifeos: boolean; binary: string | null; note: string };
  comfyui: { up: boolean; started_by_lifeos: boolean; launcher: string | null; note: string };
}

export interface AgentActivity {
  agent: AgentStatus;
  task: { id: number; title: string; instructions: string; status: string; steps: number; model: string | null; started_at: string | null } | null;
  last_tool: MissionEvent | null;
  log: MissionEvent[];
  recent_tasks: { id: number; mission_id: number; title: string; status: string; result: string; score: number | null;
    user_rating: number | null; model: string | null; steps: number; finished_at: string | null; goal: string }[];
  lessons: Lesson[];
  media: MediaItem[];
  stats: ScoreCard | null;
}

export interface Brief {
  id: number;
  mission_id: number;
  task_id: number | null;
  agent: string;
  question: string;
  options: string[];
  answer: string | null;
  status: "open" | "answered" | "cancelled";
  created_at: string;
  goal?: string;
}

export interface Mission {
  id: number;
  goal: string;
  status: "queued" | "planning" | "running" | "waiting" | "done" | "failed" | "cancelled";
  lead: string;
  target_agent: string | null;
  source: string;
  plan: { planner?: string; thinking?: string; reply?: string } | null;
  result: string | null;
  outbox_path: string | null;
  error: string | null;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  task_counts?: Record<string, number>;
  tasks?: MissionTask[];
  briefs?: Brief[];
  pending_actions?: Action[];
}

export interface Spend { tokens_in: number; tokens_out: number; cost_usd: number; cloud_cost_usd: number; cloud_budget_usd: number }

export interface CrewStatus {
  agents: AgentStatus[];
  briefs: Brief[];
  active: Mission[];
  spend: Spend;
  pending_actions: Action[];
}

export interface Provider { id: string; label: string; cloud: boolean; needs_key: boolean; has_key: boolean }

export interface OutboxFile { name: string; path: string; size: number; modified_at: string }

export interface Ledger {
  days: number;
  by_model: { model: string; provider: string; calls: number; tokens_in: number; tokens_out: number; cost_usd: number }[];
  by_agent: { agent: string; calls: number; tokens_in: number; tokens_out: number; cost_usd: number }[];
  by_day: { day: string; tokens: number; cost_usd: number }[];
  today: Spend;
}

// ---------------------------------------------------------------- automation

export type Trigger =
  | { type: "manual" }
  | { type: "startup" }
  | { type: "interval"; minutes: number }
  | { type: "daily"; time: string }
  | { type: "weekly"; days: string[]; time: string }
  | { type: "monthly"; day: number; time: string };

export interface Condition { metric: string; op: string; value: number; folder?: string; hours?: number }

export interface WorkflowStep {
  id?: string;
  type: "tool" | "mission" | "notify";
  label?: string;
  tool?: string;
  args?: Record<string, unknown>;
  goal?: string;
  agent?: string | null;
  title?: string;
  message?: string;
  retries?: number;
  retry_delay_s?: number;
  on_error?: "stop" | "continue";
  condition?: Condition;
}

export interface Workflow {
  id: number;
  name: string;
  description: string;
  enabled: boolean;
  trigger: Trigger;
  trigger_text: string;
  conditions: Condition[];
  steps: WorkflowStep[];
  source_text: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  last_run: { status: string; summary: string | null; finished_at: string | null } | null;
}

export type WorkflowDraft = Omit<Workflow, "id" | "enabled" | "last_run_at" | "next_run_at" | "last_run" | "trigger_text"> & {
  trigger_text?: string;
  planner?: string;
};

export interface WorkflowRun {
  id: number;
  workflow_id: number;
  name: string;
  status: "running" | "done" | "failed" | "skipped";
  trigger: string;
  log: { step?: string; label?: string; type?: string; status: string; summary: string; attempts?: number; mission_id?: number; action_id?: number }[];
  summary: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface WorkflowMeta { triggers: string[]; metrics: Record<string, string>; ops: string[]; step_types: string[]; days: string[] }

// ---------------------------------------------------------------- projects and integrations

export interface Project {
  id: number;
  name: string;
  path: string;
  kind: string;
  description: string;
  status: "active" | "paused" | "done";
  pinned: number;
  auto: number;
  last_activity_at: string | null;
  open_tasks: number;
}

export interface ProjectContext {
  project: Project;
  recent_files: FileItem[];
  file_count: number;
  open_tasks: Task[];
  memories: Memory[];
  readme: string | null;
}

export interface CalEvent {
  id: number;
  source_id: number | null;
  source_label?: string | null;
  uid: string;
  title: string;
  start_at: string;
  end_at: string | null;
  all_day: number;
  location: string | null;
  description: string | null;
}

export interface CalSource { id: number; kind: string; location: string; label: string | null; enabled: number; last_synced_at: string | null; last_error: string | null; event_count: number }

export interface EmailStatus { configured: boolean; host: string; port: number; username: string; folder: string }
export interface EmailMessage { id: string; from: string; subject: string; date: string | null; snippet: string }

export interface DuplicateGroup {
  sha256: string;
  size: number;
  files: FileItem[];
}

export interface SearchResults {
  memories: Memory[];
  files: FileItem[];
  tasks: Task[];
  messages: (Message & { conversation_title: string; snippet: string })[];
}

export interface DayPlan {
  day: "today" | "tomorrow";
  blocks: { task_id: number; title: string; start: string; end: string; minutes: number; due_at: string | null }[];
  planned_minutes: number;
  available_minutes: number;
}

// ---------------------------------------------------------------- endpoints

export const api = {
  health: () => get<{ ok: boolean; version: string }>("/api/health"),
  dashboard: () => get<Dashboard>("/api/dashboard"),

  chat: (message: string, conversation_id?: number | null) =>
    post<{ conversation_id: number; message: Message }>("/api/chat", { message, conversation_id }),
  conversations: () => get<Conversation[]>("/api/conversations"),
  messages: (id: number) => get<Message[]>(`/api/conversations/${id}/messages`),
  deleteConversation: (id: number) => del(`/api/conversations/${id}`),

  actions: (status?: string) => get<Action[]>(`/api/actions${qs({ status })}`),
  approve: (id: number, exclude: string[] = []) => post<Action>(`/api/actions/${id}/approve`, { exclude }),
  reject: (id: number) => post<Action>(`/api/actions/${id}/reject`),
  undo: (id: number) => post<Action>(`/api/actions/${id}/undo`),

  tasks: (status = "open") => get<Task[]>(`/api/tasks${qs({ status })}`),
  createTask: (t: Partial<Task> & { title: string; priority?: number | string }) => post<Task>("/api/tasks", t),
  updateTask: (id: number, t: Partial<Task>) => patch<Task>(`/api/tasks/${id}`, t),
  deleteTask: (id: number) => del<Task>(`/api/tasks/${id}`),
  planDay: (hours = 6) => get<DayPlan>(`/api/tasks/plan${qs({ hours })}`),

  memories: (q?: string, kind?: string) => get<Memory[]>(`/api/memories${qs({ q, kind })}`),
  addMemory: (m: { content: string; kind: string; pinned?: boolean }) => post<Memory>("/api/memories", m),
  updateMemory: (id: number, m: Partial<{ content: string; kind: string; pinned: boolean }>) =>
    patch<Memory>(`/api/memories/${id}`, m),
  deleteMemory: (id: number) => del<Memory>(`/api/memories/${id}`),

  searchFiles: (q: string, ext?: string, folder?: string) =>
    get<FileItem[]>(`/api/files/search${qs({ q, ext, folder, limit: 100 })}`),
  largeFiles: (min_mb: number, older_than_days?: number) =>
    get<FileItem[]>(`/api/files/large${qs({ min_mb, older_than_days })}`),
  duplicates: () => get<DuplicateGroup[]>("/api/files/duplicates"),
  staleFiles: (days: number) => get<FileItem[]>(`/api/files/stale${qs({ days })}`),
  readFile: (path: string) => get<{ path: string; name: string; text: string; size: number }>(`/api/files/read${qs({ path })}`),
  openFile: (path: string) => post("/api/files/open", { path }),
  reindex: () => post<Record<string, unknown>>("/api/files/index"),
  organizePreview: (path: string) => post<StepResult>("/api/files/organize/preview", { path }),

  folders: () => get<Folder[]>("/api/folders"),
  addFolder: (path: string, label?: string) => post<Folder>("/api/folders", { path, label }),
  removeFolder: (id: number) => del(`/api/folders/${id}`),

  search: (q: string) => get<SearchResults>(`/api/search${qs({ q })}`),
  activity: (kind?: string) => get<ActivityEntry[]>(`/api/activity${qs({ kind, limit: 300 })}`),

  settings: () => get<Settings>("/api/settings"),
  updateSettings: (s: Partial<Settings>) => patch<Settings>("/api/settings", s),
  pause: (paused: boolean) => post<Settings>("/api/settings/pause", { paused }),
  tools: () => get<ToolInfo[]>("/api/tools"),
  llmStatus: () => get<LlmStatus>("/api/llm/status"),

  suggestions: () => get<Suggestion[]>("/api/suggestions"),
  dismissSuggestion: (id: string) => post(`/api/suggestions/${encodeURIComponent(id)}/dismiss`),
  notifications: () => get<Notification[]>("/api/notifications"),
  markNotificationsRead: () => post("/api/notifications/read"),

  // crew
  agents: () => get<Agent[]>("/api/crew/agents"),
  createAgent: (a: Partial<Agent> & { name: string }) => post<Agent>("/api/crew/agents", a),
  updateAgent: (slug: string, a: Partial<Agent>) => patch<Agent>(`/api/crew/agents/${slug}`, a),
  deleteAgent: (slug: string) => del<Agent>(`/api/crew/agents/${slug}`),
  resetCrew: () => post<Agent[]>("/api/crew/reset"),
  crewStatus: shared(() => get<CrewStatus>("/api/crew/status")),
  gpu: shared(() => get<GpuStatus>("/api/system/gpu"), 2000),
  crewModels: () => get<{ local: string[]; ollama_ok: boolean; providers: Provider[]; cloud_enabled: boolean; default: string }>("/api/crew/models"),
  testModel: (model: string) => post<{ ok: boolean; reply?: string; seconds?: number; error?: string }>("/api/crew/test-model", { model }),
  exportPack: () => get<Record<string, unknown>>("/api/crew/pack"),
  importPack: (pack: unknown) => post<{ agents: string[]; workflows: number[]; skipped: string[] }>("/api/crew/pack", pack),

  missions: (status?: string) => get<Mission[]>(`/api/missions${qs({ status })}`),
  mission: (id: number) => get<Mission>(`/api/missions/${id}`),
  startMission: (goal: string, agent?: string | null) => post<Mission>("/api/missions", { goal, agent: agent || null }),
  cancelMission: (id: number) => post<Mission>(`/api/missions/${id}/cancel`),
  retryMission: (id: number) => post<Mission>(`/api/missions/${id}/retry`),
  stopAll: () => post<{ stopped: number }>("/api/missions/stop-all"),
  missionEvents: (after = 0, mission_id?: number) => get<MissionEvent[]>(`/api/missions/events${qs({ after, mission_id, limit: 300 })}`),
  briefs: () => get<Brief[]>("/api/briefs"),
  rateTask: (taskId: number, rating: 1 | -1, note = "") =>
    post<{ ok: boolean; lesson: Lesson | null }>(`/api/missions/tasks/${taskId}/rate`, { rating, note }),
  chatAgent: (slug: string, message: string, history: { role: "user" | "assistant"; content: string }[] = []) =>
    post<{ reply: string; model: string | null }>(`/api/crew/agents/${slug}/chat`, { message, history }),

  // learning
  learning: () => get<LearningOverview>("/api/learning"),
  lessons: (agent?: string) => get<Lesson[]>(`/api/learning/lessons${qs({ agent })}`),
  addLesson: (agent: string, text: string, pinned = false) => post<Lesson>("/api/learning/lessons", { agent, text, pinned }),
  updateLesson: (id: number, l: Partial<Pick<Lesson, "text" | "pinned" | "agent">>) => patch<Lesson>(`/api/learning/lessons/${id}`, l),
  deleteLesson: (id: number) => del(`/api/learning/lessons/${id}`),
  scorecard: () => get<ScoreCard[]>("/api/learning/scorecard"),
  exportTraining: async (minScore = 4) => {
    const res = await fetch(BASE + `/api/learning/export.jsonl${qs({ min_score: minScore })}`, { headers: { "X-LifeOS-Client": "1" } });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  answerBrief: (id: number, answer: string) => post<Brief>(`/api/briefs/${id}/answer`, { answer }),

  outbox: () => get<OutboxFile[]>("/api/outbox"),
  outboxFile: (name: string) => get<{ name: string; path: string; text: string }>(`/api/outbox/file${qs({ name })}`),
  openOutbox: (name: string) => post("/api/outbox/open", { name }),
  deleteOutbox: (name: string) => del(`/api/outbox/${encodeURIComponent(name)}`),
  ledger: (days = 7) => get<Ledger>(`/api/ledger${qs({ days })}`),

  providers: () => get<{ providers: Provider[]; vault: string; cloud_enabled: boolean }>("/api/providers"),
  setProviderKey: (id: string, key: string) => request<{ ok: boolean }>("PUT", `/api/providers/${id}/key`, { key }),
  deleteProviderKey: (id: string) => del(`/api/providers/${id}/key`),

  // automation
  workflows: () => get<Workflow[]>("/api/workflows"),
  workflow: (id: number) => get<Workflow>(`/api/workflows/${id}`),
  createWorkflow: (w: Partial<Workflow>) => post<Workflow>("/api/workflows", w),
  updateWorkflow: (id: number, w: Partial<Workflow>) => patch<Workflow>(`/api/workflows/${id}`, w),
  deleteWorkflow: (id: number) => del<Workflow>(`/api/workflows/${id}`),
  runWorkflow: (id: number) => post<{ started: boolean }>(`/api/workflows/${id}/run`),
  workflowRuns: (id?: number) => get<WorkflowRun[]>(id ? `/api/workflows/${id}/runs` : "/api/workflow-runs"),
  draftWorkflow: (text: string) => post<WorkflowDraft>("/api/workflows/draft", { text }),
  workflowTemplates: () => get<WorkflowDraft[]>("/api/workflows/templates"),
  workflowMeta: () => get<WorkflowMeta>("/api/workflows/meta"),

  // projects
  projects: (status?: string) => get<Project[]>(`/api/projects${qs({ status })}`),
  project: (id: number) => get<ProjectContext>(`/api/projects/${id}`),
  addProject: (path: string, name?: string) => post<Project>("/api/projects", { path, name }),
  detectProjects: () => post<{ found: number; added: number; removed: number }>("/api/projects/detect"),
  updateProject: (id: number, p: Partial<Pick<Project, "name" | "description" | "status">> & { pinned?: boolean }) =>
    patch<Project>(`/api/projects/${id}`, p),
  deleteProject: (id: number) => del<Project>(`/api/projects/${id}`),

  // calendar and email
  calendarSources: () => get<CalSource[]>("/api/calendar/sources"),
  addCalendarSource: (location: string, label?: string) => post<CalSource>("/api/calendar/sources", { location, label }),
  removeCalendarSource: (id: number) => del(`/api/calendar/sources/${id}`),
  syncCalendarSource: (id: number) => post<{ ok: boolean; events?: number; error?: string }>(`/api/calendar/sources/${id}/sync`),
  events: (start?: string, end?: string) => get<CalEvent[]>(`/api/events${qs({ start, end })}`),
  addEvent: (e: { title: string; start: string; end?: string; location?: string }) => post<CalEvent>("/api/events", e),
  deleteEvent: (id: number) => del(`/api/events/${id}`),
  overlaps: () => get<{ a: CalEvent; b: CalEvent }[]>("/api/calendar/overlaps"),
  exportIcs: async () => {
    const res = await fetch(BASE + "/api/calendar/export.ics", { headers: { "X-LifeOS-Client": "1" } });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  // media
  media: (kind?: string, limit = 60, agent?: string, since?: string) => get<MediaItem[]>(`/api/media${qs({ kind, limit, agent, since })}`),
  mediaItem: (id: number) => get<MediaItem>(`/api/media/${id}`),
  mediaStatus: () => get<MediaStatus>("/api/media/status"),
  generateMedia: (m: { kind: "image" | "video"; prompt: string; negative?: string; size?: string; seconds?: number; seed?: number | null }) =>
    post<MediaItem>("/api/media", m),
  setMediaSettings: (m: Partial<MediaSettings>) => patch<MediaSettings>("/api/media/settings", m),
  deleteMedia: (id: number) => del(`/api/media/${id}`),
  openMedia: (id: number) => post(`/api/media/${id}/open`),
  // Files need the client header, so they're fetched as blobs and shown through object URLs.
  mediaBlob: async (id: number) => {
    const res = await fetch(BASE + `/api/media/${id}/file`, { headers: { "X-LifeOS-Client": "1" } });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.blob();
  },
  services: () => get<Services>("/api/system/services"),
  startService: (name: "ollama" | "comfyui") => post<Services & { ok: boolean }>(`/api/system/services/${name}/start`),
  oldData: () => get<OldData>("/api/system/old-data"),
  importOldData: () => post<OldData>("/api/system/old-data/import"),
  updateManifest: () => get<unknown>("/api/system/update/manifest"),
  updateConfig: () => get<UpdateConfig>("/api/system/update/config"),
  setUpdateConfig: (c: { repo?: string; token?: string }) => patch<UpdateConfig>("/api/system/update/config", c),
  agentActivity: (slug: string) => get<AgentActivity>(`/api/crew/agents/${slug}/activity`),

  emailStatus: () => get<EmailStatus>("/api/email/status"),
  setEmail: (e: { host: string; port: number; username: string; folder: string; password?: string }) =>
    request<EmailStatus>("PUT", "/api/email", e),
  testEmail: () => post<{ ok: boolean; latest?: string | null; error?: string }>("/api/email/test"),
  emailMessages: (limit = 15, unread = true) => get<EmailMessage[]>(`/api/email/messages${qs({ limit, unread })}`),
};
