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

const get = <T>(p: string) => request<T>("GET", p);
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
}

export interface Settings {
  ollama_url: string;
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
}

export interface ToolInfo {
  name: string;
  description: string;
  risk: "read" | "write" | "destructive";
  default_policy: string;
  policy: string;
}

export interface Notification {
  id: number;
  title: string;
  body: string | null;
  task_id: number | null;
  read: number;
  created_at: string;
}

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
};
