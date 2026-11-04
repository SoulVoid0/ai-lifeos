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

export interface ModelPull { model: string; state: "pulling" | "done" | "failed"; status: string; completed: number; total: number; error: string | null }
export interface OllamaTuning { wanted: Record<string, string>; applied: Record<string, string> | null; pending_restart: boolean; kv_cache: string; flash_attention_refused: boolean }
export interface LeanStatus {
  on: boolean;
  plan: { agent: string; slug: string; model: string; installed: boolean; assigned: boolean }[];
  pulls: ModelPull[];
  tuning: OllamaTuning;
  removable: string[];
  think: boolean;
  fast_model: string;
  downloading?: string[];
  gpu_hint?: string | null;
  tuning_result?: unknown;
}
export interface InstalledModel { name: string; size: number; in_use: boolean }

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
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
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
  parent_id?: number | null;
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
  action?: { label: string; method: string; path: string; body?: unknown };
}

export interface WorkspaceItem { kind: "file" | "folder" | "url" | "app"; target: string; label?: string }
export interface Workspace { id: number; name: string; items: WorkspaceItem[]; last_opened_at?: string | null; open_count?: number }
export interface CloudFolder { provider: string; path: string; approved: boolean }
export interface CustomTool {
  id: number; name: string; kind: "webhook" | "prompt"; description: string; enabled: boolean; has_secret: boolean;
  config: { url?: string; method?: string; body?: string; secret_header?: string; template?: string };
}
export interface AppEntry { id: string; name: string; source: string; launches: number; last_launched_at: string | null; comment?: string }
export interface SystemStatus {
  cpu_percent: number; cpu_count: number; uptime_hours: number; platform: string;
  memory: { used_gb: number; total_gb: number; percent: number };
  disk: { path: string; free_gb: number; total_gb: number; percent: number };
  battery: { percent: number; plugged_in: boolean; minutes_left: number | null } | null;
}
export interface Program { name: string; pids: number[]; count: number; memory_mb: number; cpu_percent: number; protected: boolean }
export type PowerAction = "lock" | "sleep" | "restart" | "shutdown" | "sign_out";
export interface WifiNetwork { ssid: string; signal: number; secure: boolean; security: string; connected: boolean }
export interface BtDevice { address: string; name: string; paired: boolean; connected: boolean; kind: string; battery: number | null }
export interface DisplayInfo {
  name: string; label: string; enabled: boolean; scale: number | null; width: number | null; height: number | null;
  refresh: number | null; modes: string[]; settings_only?: boolean;
}
type Maybe<T> = T | { error: string };
export interface DevicesOverview {
  platform: "windows" | "linux" | "macos";
  wifi: Maybe<{ available: boolean; enabled: boolean; connected: string }>;
  bluetooth: Maybe<{ available: boolean; powered: boolean | null; settings_only?: boolean }>;
  displays: Maybe<DisplayInfo[]>;
  brightness: number | null | { error: string };
  notifications: { supported: boolean; running: boolean; error: string };
}
export type BtAction = "connect" | "disconnect" | "pair" | "forget" | "power_on" | "power_off";
export interface SetupState { needed: boolean; done: boolean; user_name: string; suggested_folders: string[]; platform: string }
export interface BackupFile { name: string; path: string; bytes: number; created_at: string }

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
  ollama_gpu_gb: number;
  ollama_cpu_threads: number;
  ollama_low_priority: boolean;
  ollama_unload_on_switch: boolean;
  ollama_hard_stop: boolean;
  ollama_model_options: Record<string, { num_ctx?: number; num_gpu?: number; num_thread?: number }>;
  ollama_think: boolean;
  ollama_tuning: boolean;
  ollama_flash_attention: boolean;
  ollama_kv_cache: "f16" | "q8_0" | "q4_0";
  fast_model: string;
  stay_on_loaded_model: boolean;
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
  backup_folder: string;
  backup_daily: boolean;
  backup_keep: number;
  backup_include_media: boolean;
  backup_last_at: string;
  speak_replies: boolean;
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
  tts_voice?: string;
  tts_rate?: number;
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
export type EmailAlertEvent = "mission_done" | "mission_failed" | "approval" | "question" | "workflow";
export interface EmailAlerts {
  enabled: boolean; to: string; host: string; port: number; username: string; security: "ssl" | "starttls"; buttons: boolean;
  events: Record<EmailAlertEvent, boolean>;
  configured: boolean; password_stored: boolean; password_from_imap: boolean; last_sent_at: string | null; last_error: string | null;
  buttons_inbox: string | null; replies: { running: boolean; last_poll: string | null; last_error: string | null };
}
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

// ---------------------------------------------------------------- 0.2 features

export interface VoiceConfig {
  wake_word: boolean; wake_phrase: string; mic: string; stt_model: "small" | "lgraph"; engine: "auto" | "piper" | "system" | "browser";
  default_voice: string; chime: boolean; speak_agent_chat: boolean; pause_in_games: boolean; listen_seconds: number;
}
export interface VoiceJob { item: string; state: "downloading" | "done" | "failed"; done: number; total: number; error: string | null }
export interface VoiceExchange { heard: string; reply: string; agent: string | null; kind: string; at: string }
export interface VoiceStatus {
  config: VoiceConfig; piper: boolean; voices_installed: string[];
  catalog: { id: string; label: string; installed: boolean }[];
  stt_models: { key: string; name: string; size: string; label: string; installed: boolean }[];
  packages: { vosk: boolean; sounddevice: boolean }; system_tts: string | null; jobs: VoiceJob[];
  listener: { state: string; heard: string; reply: string; agent: string | null; error: string; level: number; since: number };
  history: VoiceExchange[]; agent_voices: Record<string, { voice: string; rate: number }>;
}
export interface Preset { id: string; label: string; tts_voice: string; tts_rate: number; voice: string; quirks: string; catchphrases: string[]; idle_lines: string[] }
export interface ScreenStatus {
  config: { model: string; keep_screenshot: boolean; hotkey: boolean };
  models: { model: string; size: string; why: string }[]; installed: boolean;
  last: { answer?: string; question?: string; model?: string; busy?: boolean; at?: number; seconds?: number };
  screenshot_at: number | null; hotkey: string | null;
}
export interface ScreenAnswer { answer: string; model: string; question: string; seconds: number }
export interface ArenaMatch {
  id: number; prompt: string; a_agent: string; a_model: string; b_agent: string; b_model: string;
  a_result: string | null; b_result: string | null; a_seconds: number | null; b_seconds: number | null;
  status: "running" | "ready" | "decided" | "failed"; error: string | null; winner: "a" | "b" | "tie" | null; reason: string | null;
  a_name: string | null; b_name: string | null; created_at: string; new_lessons?: { id: number; agent: string; text: string }[];
}
export interface ArenaRank { key: string; name: string; win: number; loss: number; tie: number; games: number; win_rate: number | null }
export interface TidyConfig { enabled: boolean; folder: string; mode: "ask" | "auto"; rename: boolean; min_age_minutes: number; interval_minutes: number; last_run_at: string }
export interface TidyStatus { config: TidyConfig; folder: string; loose_files: number; default_folder: string; os_downloads: string | null }
export interface TidyPlan { folder: string; moves: (Move & { renamed: boolean })[]; by_category: Record<string, number>; renamed: number }
export interface RemoteDevice { id: string; name: string; created_at: string; last_seen: string; ip: string }
export interface RemoteStatus { enabled: boolean; running: boolean; port: number; urls: string[]; pin: string | null; pin_expires_in: number | null; pair_url: string | null; devices: RemoteDevice[]; error: string }
export interface GameModeConfig { enabled: boolean; auto_detect: boolean; fullscreen: boolean; games: string[]; free_gpu: boolean; stop_comfy: boolean; hold_ai: boolean; reload_after: boolean; notify: boolean }
export interface GameMode { active: boolean; game: string | null; since: number | null; reason: string | null; manual: boolean | null; config: GameModeConfig; unloaded: number }
export interface WallpaperConfig { enabled: boolean; fps: number; show_clock: boolean; show_ticker: boolean; dim: number; game_mode?: boolean }
export interface UpgradeConfig { enabled: boolean; repo_path: string; every_days: number; focus: string; run_tests: boolean; push: boolean; model: string; last_run_at: string }
export interface UpgradeStatus {
  config: UpgradeConfig; repo: string | null; git: boolean; model: string | null; busy: boolean;
  last: { state: string; error: string; at: string | null; action_id: number | null }; proposals: Action[];
}
export interface RecallHit { source: "web" | "chat" | "file" | "memory" | "mission" | "activity"; title: string; url?: string; path?: string; snippet?: string; when: string; detail?: string; score: number; mission_id?: number; conversation_id?: number }
export interface RecallAnswer { question: string; answer: string; keywords: string[]; window: { start: string | null; end: string | null; label: string }; widened: string | null; results: RecallHit[]; history_on: boolean }
export interface RecallStatus { config: { browser_history: boolean; keep_days: number; last_sync: string }; visits: number; per_browser: Record<string, number>; browsers_found: string[] }

// ---------------------------------------------------------------- 0.3 features

export interface CouncilTurn { round: number; agent: string; text: string; seconds: number }
export interface CouncilVote { agent: string; choice: number; reason: string }
export interface CouncilSession {
  id: number; question: string; agents: string[]; rounds: number; transcript: CouncilTurn[]; options: string[]; votes: CouncilVote[];
  tally: number[]; decision: string | null; summary: string | null; user_choice: string | null; names: Record<string, string>;
  status: "running" | "voting" | "decided" | "failed" | "stopped"; error: string | null; created_at: string; finished_at: string | null;
}
export interface XpAgent {
  slug: string; name: string; role: string; color: string; icon: string; level: number; rank: string; xp: number; week_xp: number;
  level_start: number; next_level: number; progress: number; avg_score: number | null; success_rate: number | null;
  tasks_done: number; tasks_failed: number; good_tasks: number; rated_high: number; arena_wins: number; arena_losses: number;
  arena_ties: number; councils: number; council_majority: number; lessons: number; work_seconds: number; steps: number;
}
export interface XpBoard { agents: XpAgent[]; crew_xp: number; crew_level: number; xp_table: Record<string, number> }
export interface MoodConfig { enabled: boolean; colors: boolean; music: boolean; volume: number; follow_time: boolean; follow_activity: boolean }
export interface Mood {
  config: MoodConfig; phase: "night" | "dawn" | "day" | "dusk"; activity: "idle" | "busy" | "alert" | "focus" | "gaming";
  counts: { working: number; waiting: number }; palette: { accent: string; tint: string; glow: string };
  music: { on: boolean; volume: number; tempo: number; intensity: number; scale: string; root: number }; hour: number;
}
export interface SfxConfig { enabled: boolean; volume: number; theme: "scifi" | "soft" | "retro"; clicks: boolean }
export interface PetConfig { enabled: boolean; species: "bot" | "cat" | "slime" | "ghost" | "fox"; name: string; size: number; roam: boolean; speak: boolean }
export interface MiniApp { id: number; name: string; description: string; icon: string; version: number; status: "building" | "ready" | "failed"; error: string | null; notes: string; busy: boolean; created_at: string; updated_at: string }
export interface DoctorFinding { severity: "ok" | "info" | "warn" | "bad"; area: string; title: string; detail: string; fix: string }
export interface DoctorReport { at: string; worst: DoctorFinding["severity"]; findings: DoctorFinding[]; summary: string; seconds: number; counts: Record<string, number> }
export interface DoctorStatus { config: { weekly: boolean; last_run_at: string; notify: boolean }; busy: boolean; report: DoctorReport | null }
export interface Deck { id: number; title: string; source: string; status: "making" | "ready" | "failed"; error: string | null; created_at: string; cards: number; due: number; new: number; learned: number; card_list?: Card[] }
export interface Card { id: number; deck_id: number; front: string; back: string; ease: number; interval_days: number; reps: number; lapses: number; due_at: string; deck_title?: string }
export interface LearnStats { cards: number; due: number; learned: number; streak: number; reviews_week: number; accuracy_week: number | null; config: { daily_reminder: boolean; new_per_day: number } }
export interface StoryScene { id: number; idx: number; text: string; choices: string[]; chosen: string | null; image_prompt: string; media_id: number | null; image_status: string | null; image_error: string | null }
export interface Story { id: number; title: string; premise: string; style: string; narrator: string; status: "writing" | "ready" | "ended" | "failed"; error: string | null; created_at: string; scene_count: number; scenes?: StoryScene[]; cover_media_id?: number | null }
export interface PanicStatus { active: boolean; since: string | null; automation_paused: boolean; config: { hotkey: boolean; hide_windows: boolean; pause_automation: boolean; unload_models: boolean; mute: boolean; stop_media: boolean }; done?: string[]; hide_windows?: boolean }
export interface UsbDrive { id: string; label: string; allowed: boolean; present: boolean; mount?: string; free?: number; total?: number; last_backup_at?: string; last_bytes?: number }
export interface UsbStatus { config: { enabled: boolean; include_media: boolean; keep: number; ask_new: boolean; min_hours: number }; drives: UsbDrive[]; running: string | null; last: { path: string; bytes: number; drive: string; seconds: number } | null; error: string }

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
  apps: (q = "", refresh = false) => get<AppEntry[]>(`/api/apps${qs({ q, refresh: refresh || undefined })}`),
  launchApp: (id: string) => post<{ opened: string }>("/api/apps/launch", { id }),
  systemStatus: () => get<SystemStatus>("/api/system/status"),
  programs: (sort: "memory" | "cpu" = "memory") => get<Program[]>(`/api/system/programs${qs({ sort })}`),
  endProgram: (name: string) => post<{ ended: string; processes: number }>("/api/system/programs/end", { name }),
  setVolume: (v: { level?: number; change?: "up" | "down" | "mute" }) => post("/api/system/volume", v),
  power: (action: PowerAction) => post<{ summary: string }>("/api/system/power", { action }),
  cloudFolders: () => get<CloudFolder[]>("/api/folders/cloud"),
  devices: () => get<DevicesOverview>("/api/devices"),
  wifiNetworks: (rescan = false) => get<WifiNetwork[]>(`/api/devices/wifi${qs({ rescan: rescan || undefined })}`),
  wifiConnect: (ssid: string, password = "") => post<{ connected: string }>("/api/devices/wifi/connect", { ssid, password }),
  wifiDisconnect: () => post("/api/devices/wifi/disconnect"),
  wifiPower: (on: boolean) => post<{ enabled: boolean; opened_settings?: boolean }>("/api/devices/wifi/power", { on }),
  btDevices: (scan = 0) => get<BtDevice[]>(`/api/devices/bluetooth${qs({ scan: scan || undefined })}`),
  btAction: (action: BtAction, device = "") => post<{ summary: string }>("/api/devices/bluetooth", { action, device }),
  setDisplay: (d: { name: string; mode?: string; scale?: number; enabled?: boolean }) => post<{ summary: string }>("/api/devices/display", d),
  setBrightness: (level: number) => post("/api/devices/brightness", { level }),
  openOsSettings: (page: "wifi" | "bluetooth" | "display" | "sound") => post(`/api/devices/open-settings/${page}`),
  setup: () => get<SetupState>("/api/setup"),
  finishSetup: () => post("/api/setup/done"),

  suggestionAction: (a: NonNullable<Suggestion["action"]>) => request<unknown>(a.method, a.path, a.body ?? {}),

  workspaces: () => get<Workspace[]>("/api/workspaces"),
  saveWorkspace: (w: { name: string; items: WorkspaceItem[] }, id?: number) =>
    id ? put<Workspace>(`/api/workspaces/${id}`, w) : post<Workspace>("/api/workspaces", w),
  deleteWorkspace: (id: number) => del(`/api/workspaces/${id}`),
  openWorkspace: (id: number) => post<{ workspace: string; opened: string[]; failed: { target: string; error: string }[] }>(`/api/workspaces/${id}/open`),
  automateWorkspace: (id: number, time: string, days: string[]) => post(`/api/workspaces/${id}/automate`, { time, days }),

  customTools: () => get<CustomTool[]>("/api/custom-tools"),
  saveCustomTool: (t: Partial<CustomTool> & { secret?: string | null }, id?: number) =>
    id ? put<CustomTool>(`/api/custom-tools/${id}`, t) : post<CustomTool>("/api/custom-tools", t),
  deleteCustomTool: (id: number) => del(`/api/custom-tools/${id}`),
  exportCustomTool: (id: number) => get<Record<string, unknown>>(`/api/custom-tools/${id}/export`),
  importCustomTool: (data: unknown) => post<CustomTool>("/api/custom-tools/import", data),
  testCustomTool: (id: number, input: string) => post<{ status?: number; response: string }>(`/api/custom-tools/${id}/test`, { input }),

  backups: () => get<{ backups: BackupFile[]; pending_restore: string | null }>("/api/backups"),
  backupNow: (include_media?: boolean) => post<{ path: string; bytes: number }>("/api/backups", { include_media }),
  restoreBackup: (path: string) => post<{ pending: string }>("/api/backups/restore", { path }),

  breakDownTask: (id: number) => post<{ subtasks: Task[] }>(`/api/tasks/${id}/breakdown`),
  replanTask: (id: number) => post<{ task: Task; moved: unknown[] }>(`/api/tasks/${id}/replan`),

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
  lean: () => get<LeanStatus>("/api/system/lean"),
  applyLean: (download = true) => post<LeanStatus>("/api/system/lean", { download }),
  applyTuning: () => post<OllamaTuning & { restarted: boolean; note?: string }>("/api/system/ollama/tuning"),
  installedModels: () => get<{ models: InstalledModel[]; pulls: ModelPull[] }>("/api/system/models"),
  pullModel: (model: string) => post<ModelPull>("/api/system/models/pull", { model }),
  removeModel: (model: string) => del<{ removed: string }>(`/api/system/models/${encodeURIComponent(model)}`),
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
  emailAlerts: () => get<EmailAlerts>("/api/email-alerts"),
  setEmailAlerts: (e: Partial<Omit<EmailAlerts, "configured" | "password_stored" | "password_from_imap" | "last_sent_at" | "last_error" | "buttons_inbox" | "replies">> & { password?: string }) =>
    request<EmailAlerts>("PUT", "/api/email-alerts", e),
  testEmailAlerts: () => post<{ ok: boolean; to?: string; error?: string }>("/api/email-alerts/test"),
  emailMessages: (limit = 15, unread = true) => get<EmailMessage[]>(`/api/email/messages${qs({ limit, unread })}`),

  // 0.2
  voice: () => get<VoiceStatus>("/api/voice"),
  setVoice: (c: Partial<VoiceConfig>) => patch<VoiceStatus>("/api/voice", c),
  microphones: () => get<{ index: number; name: string; default: boolean }[]>("/api/voice/microphones"),
  systemVoices: () => get<string[]>("/api/voice/system-voices"),
  installVoice: (items: string[]) => post<VoiceStatus>("/api/voice/install", { items }),
  say: (text: string, agent?: string | null, voice?: string, rate?: number) => post<{ queued: boolean; engine?: string; text?: string }>("/api/voice/say", { text, agent: agent ?? null, voice, rate }),
  stopSpeaking: () => post("/api/voice/stop"),
  listen: () => post<{ ok: boolean }>("/api/voice/listen"),
  voiceCommand: (text: string, speak = true) => post<{ reply: string; agent: string | null; route: { kind: string } }>("/api/voice/command", { text, speak }),
  voiceAudioUrl: (text: string, agent?: string | null) => `${BASE}/api/voice/audio${qs({ text, agent })}`,
  presets: () => get<Preset[]>("/api/crew/presets"),
  screen: () => get<ScreenStatus>("/api/screen"),
  setScreen: (c: Partial<ScreenStatus["config"]>) => patch<ScreenStatus>("/api/screen", c),
  askScreen: (question: string, fresh?: boolean) => post<ScreenAnswer>("/api/screen/ask", { question, fresh }),
  captureScreen: () => post<{ width: number; height: number }>("/api/screen/capture"),
  screenshotBlob: async () => {
    const res = await fetch(`${BASE}/api/screen/last.png?t=${Date.now()}`, { headers: { "X-LifeOS-Client": "1" } });
    if (!res.ok) throw new ApiError(res.status, "No screenshot yet");
    return URL.createObjectURL(await res.blob());
  },
  arena: () => get<ArenaMatch[]>("/api/arena"),
  arenaMatch: (id: number) => get<ArenaMatch>(`/api/arena/${id}`),
  startArena: (prompt: string, a: { agent: string; model?: string }, b: { agent: string; model?: string }) => post<ArenaMatch>("/api/arena", { prompt, a, b }),
  voteArena: (id: number, winner: "a" | "b" | "tie", reason = "") => post<ArenaMatch>(`/api/arena/${id}/vote`, { winner, reason }),
  deleteArena: (id: number) => del(`/api/arena/${id}`),
  arenaBoard: () => get<{ agents: ArenaRank[]; models: ArenaRank[]; rounds: number }>("/api/arena/leaderboard"),
  tidy: () => get<TidyStatus>("/api/tidy"),
  setTidy: (c: Partial<TidyConfig>) => patch<TidyStatus>("/api/tidy", c),
  tidyPreview: () => get<TidyPlan>("/api/tidy/preview"),
  tidyRun: () => post<{ status: string; action?: Action }>("/api/tidy/run"),
  remote: () => get<RemoteStatus>("/api/remote"),
  setRemote: (c: { enabled?: boolean; port?: number }) => patch<RemoteStatus>("/api/remote", c),
  newRemotePin: () => post<RemoteStatus>("/api/remote/pin"),
  remoteQr: async () => {
    const res = await fetch(`${BASE}/api/remote/qr.svg?t=${Date.now()}`, { headers: { "X-LifeOS-Client": "1" } });
    if (!res.ok) throw new ApiError(res.status, "No QR code");
    return URL.createObjectURL(await res.blob());
  },
  removeRemoteDevice: (id: string) => del<RemoteDevice[]>(`/api/remote/devices/${id}`),
  remoteFirewall: () => post<{ ok: boolean; note: string }>("/api/remote/firewall"),
  gameMode: shared(() => get<GameMode>("/api/gamemode"), 2000),
  setGameModeConfig: (c: Partial<GameModeConfig>) => patch<GameMode>("/api/gamemode", c),
  setGameMode: (on: boolean | null) => post<GameMode>("/api/gamemode", { on }),
  detectGame: () => get<{ game: string | null; how: string | null }>("/api/gamemode/detect"),
  wallpaper: () => get<WallpaperConfig>("/api/wallpaper"),
  setWallpaperConfig: (c: Partial<WallpaperConfig>) => patch<WallpaperConfig>("/api/wallpaper", c),
  upgrade: () => get<UpgradeStatus>("/api/upgrade"),
  setUpgrade: (c: Partial<UpgradeConfig>) => patch<UpgradeStatus>("/api/upgrade", c),
  proposeUpgrade: (focus?: string) => post<UpgradeStatus>("/api/upgrade/propose", { focus: focus ?? null }),
  recall: () => get<RecallStatus>("/api/recall"),
  setRecall: (c: Partial<RecallStatus["config"]>) => patch<RecallStatus>("/api/recall", c),
  recallSearch: (question: string) => post<RecallAnswer>("/api/recall/search", { question }),
  recallSync: () => post<{ added: number; browsers: string[]; errors?: string[] }>("/api/recall/sync"),
  forgetHistory: () => del<{ deleted: number }>("/api/recall/history"),

  // 0.3
  councils: () => get<CouncilSession[]>("/api/council"),
  council: (id: number) => get<CouncilSession>(`/api/council/${id}`),
  councilDefaults: () => get<{ members: string[]; max: number }>("/api/council/defaults"),
  startCouncil: (question: string, members: string[], rounds: number) => post<CouncilSession>("/api/council", { question, members, rounds }),
  stopCouncil: (id: number) => post<CouncilSession>(`/api/council/${id}/stop`),
  chooseCouncil: (id: number, option: number | null) => post<CouncilSession>(`/api/council/${id}/choose`, { option }),
  councilMission: (id: number) => post<{ id: number }>(`/api/council/${id}/mission`),
  deleteCouncil: (id: number) => del(`/api/council/${id}`),
  mood: () => get<Mood>("/api/mood"),
  setMood: (c: Partial<MoodConfig>) => patch<Mood>("/api/mood", c),
  xp: () => get<XpBoard>("/api/xp"),
  sfx: () => get<SfxConfig>("/api/sfx"),
  setSfx: (c: Partial<SfxConfig>) => patch<SfxConfig>("/api/sfx", c),
  pet: () => get<PetConfig>("/api/pet"),
  setPet: (c: Partial<PetConfig>) => patch<PetConfig>("/api/pet", c),
  miniApps: () => get<MiniApp[]>("/api/mini-apps"),
  miniApp: (id: number) => get<MiniApp>(`/api/mini-apps/${id}`),
  buildMiniApp: (description: string) => post<MiniApp>("/api/mini-apps", { description }),
  reviseMiniApp: (id: number, change: string) => post<MiniApp>(`/api/mini-apps/${id}/revise`, { change }),
  undoMiniApp: (id: number) => post<MiniApp>(`/api/mini-apps/${id}/undo`),
  deleteMiniApp: (id: number) => del(`/api/mini-apps/${id}`),
  miniAppStore: (id: number) => get<Record<string, unknown>>(`/api/mini-apps/${id}/store`),
  setMiniAppStore: (id: number, key: string, value: unknown, remove = false) => post<Record<string, unknown>>(`/api/mini-apps/${id}/store`, { key, value, remove }),
  miniAppUrl: (id: number, version: number) => `${BASE}/miniapp/${id}?v=${version}`,
  doctor: () => get<DoctorStatus>("/api/doctor"),
  setDoctor: (c: Partial<DoctorStatus["config"]>) => patch<DoctorStatus>("/api/doctor", c),
  runDoctor: () => post<DoctorReport>("/api/doctor/run"),
  cleanTemp: () => post<{ removed: number; freed: number; skipped: number; folder: string }>("/api/doctor/clean-temp"),
  learn: () => get<{ stats: LearnStats; decks: Deck[] }>("/api/learn"),
  setLearn: (c: Partial<LearnStats["config"]>) => patch<LearnStats>("/api/learn", c),
  deck: (id: number) => get<Deck>(`/api/learn/decks/${id}`),
  makeDeck: (d: { topic?: string; text?: string; path?: string; count?: number; title?: string }) => post<Deck>("/api/learn/decks", d),
  deleteDeck: (id: number) => del(`/api/learn/decks/${id}`),
  addCard: (deck: number, front: string, back: string) => post<Deck>(`/api/learn/decks/${deck}/cards`, { front, back }),
  editCard: (id: number, c: { front?: string; back?: string }) => patch<Card>(`/api/learn/cards/${id}`, c),
  deleteCard: (id: number) => del(`/api/learn/cards/${id}`),
  importCards: (deck: number, text: string) => post<{ added: number }>(`/api/learn/decks/${deck}/import`, { text }),
  deckExportUrl: (id: number) => `${BASE}/api/learn/decks/${id}/export`,
  learnQueue: (deck?: number) => get<Card[]>(`/api/learn/queue${qs({ deck_id: deck })}`),
  reviewCard: (id: number, grade: number) => post<Card>(`/api/learn/cards/${id}/review`, { grade }),
  checkCard: (id: number, answer: string) => post<{ correct: boolean; feedback: string; suggested_grade: number; back: string }>(`/api/learn/cards/${id}/check`, { answer }),
  stories: () => get<{ stories: Story[]; styles: string[] }>("/api/stories"),
  story: (id: number) => get<Story>(`/api/stories/${id}`),
  startStory: (premise: string, style: string, narrator: string, pictures: boolean) => post<Story>("/api/stories", { premise, style, narrator, pictures }),
  chooseStory: (id: number, choice: string, pictures = true) => post<Story>(`/api/stories/${id}/choose`, { choice, pictures }),
  retryStory: (id: number) => post<Story>(`/api/stories/${id}/retry`),
  redrawScene: (sceneId: number) => post(`/api/stories/scenes/${sceneId}/picture`),
  deleteStory: (id: number) => del(`/api/stories/${id}`),
  storyMarkdownUrl: (id: number) => `${BASE}/api/stories/${id}/markdown`,
  panic: shared(() => get<PanicStatus>("/api/panic"), 1500),
  triggerPanic: () => post<PanicStatus>("/api/panic"),
  clearPanic: () => post<PanicStatus>("/api/panic/clear"),
  setPanic: (c: Partial<PanicStatus["config"]>) => patch<PanicStatus>("/api/panic", c),
  usb: () => get<UsbStatus>("/api/usb-backup"),
  setUsb: (c: Partial<UsbStatus["config"]>) => patch<UsbStatus>("/api/usb-backup", c),
  allowUsb: (id: string, allowed: boolean) => post<UsbStatus>(`/api/usb-backup/${id}/allow`, { allowed }),
  runUsb: (id: string) => post<{ path: string; bytes: number }>(`/api/usb-backup/${id}/run`),
  forgetUsb: (id: string) => del<UsbStatus>(`/api/usb-backup/${id}`),
};
