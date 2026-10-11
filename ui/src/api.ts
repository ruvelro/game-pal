// Cliente de la API local del daemon. El token llega en el fragmento de la URL y nunca se envía al servidor
// como parte de la dirección: se guarda en sessionStorage y se usa en la cabecera Authorization.

const TOKEN_KEY = "gamepal.token";

function readToken(): string | null {
  const match = /(?:^|&)token=([^&]+)/.exec(window.location.hash.slice(1));
  if (match?.[1]) {
    sessionStorage.setItem(TOKEN_KEY, decodeURIComponent(match[1]));
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return sessionStorage.getItem(TOKEN_KEY);
}

export const token = readToken();

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      Authorization: `Bearer ${token ?? ""}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = (await response.json()) as { detail?: unknown };
      if (data.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* sin cuerpo JSON */
    }
    throw new ApiError(response.status, detail);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? response.json() : response.blob()) as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: async <T>(path: string, files: FileList): Promise<T> => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    const response = await fetch(path, { method: "POST", headers: { Authorization: `Bearer ${token ?? ""}` }, body: form });
    if (!response.ok) {
      const data = (await response.json().catch(() => ({}))) as { detail?: string };
      throw new ApiError(response.status, data.detail ?? response.statusText);
    }
    return (await response.json()) as T;
  },
};

export interface EvidenceFact {
  key: string;
  kind: string;
  subject: string;
  label: string;
  value: string | null;
  rank: number;
  origin: "save" | "player" | "screen" | "offer";
  details: Record<string, string | number | boolean | null>;
  evidence: string;
}

export interface EvidenceSnapshot {
  run: string | null;
  facts: EvidenceFact[];
  offers: EvidenceFact[];
  save: {
    at: number | null;
    path: string | null;
    folder?: string | null;
    error?: string;
    reader?: boolean;
    declared?: boolean;
    untrusted?: { profile: string; digest: string } | null;
    summary: { choices?: Array<{ chosen: string | null; label: string | null; rarity: string | null }>; choices_belong_to?: string; in_run?: boolean; map?: string };
  };
}

export interface MemorySession {
  id: number;
  started_at: number;
  ended_at: number | null;
  summary: string | null;
  turns: number;
}

export interface MemoryInfo {
  enabled: boolean;
  current: number | null;
  sessions: MemorySession[];
  lessons: Array<{ id: number; text: string; created_at: number }>;
}

export interface KnowledgeSource {
  id: number;
  kind: "url" | "file";
  location: string;
  origin: "profile" | "user";
  options: { depth?: number; max_pages?: number | null };
  status: "pending" | "queued" | "running" | "ready" | "error";
  error: string;
  updated_at: number | null;
  pages: number;
  chunks: number;
}

export interface KnowledgeInfo {
  sources: KnowledgeSource[];
  stats: { pages: number; chunks: number };
  embeddings: "local" | "provider" | "none";
  embedder_error: string;
  refresh: string;
  current: number | null;
}

export interface KnowledgeProgress {
  profile: string;
  source_id: number;
  location: string;
  state: "queued" | "running" | "ready" | "error";
  pages: number;
  message: string;
}

export interface SearchHit {
  title: string;
  heading: string;
  text: string;
  url: string;
  score: number;
}

export type Component =
  | "capture"
  | "ocr"
  | "provider"
  | "profile"
  | "daemon"
  | "voice"
  | "overlay"
  | "hotkeys"
  | "knowledge"
  | "save"
  | "memory";
export type State = "idle" | "starting" | "running" | "paused" | "waiting" | "error" | "ready";

export interface StatusEvent {
  component: Component;
  state: State;
  detail: string;
  /** De 0 a 1 mientras se descarga algo. */
  progress?: number | null;
}

export interface TextLine {
  text: string;
  confidence: number;
  box: { x: number; y: number; width: number; height: number };
}

export interface ScreenRead {
  frame_id: number;
  backend: string;
  elapsed_ms: number;
  lines: TextLine[];
}

export interface WindowSeen {
  title: string | null;
  process: string | null;
  width: number;
  height: number;
  foreground: boolean;
}

export interface FrameCaptured {
  frame_id: number;
  width: number;
  height: number;
  distance: number;
  changed: boolean;
}

export interface VocabValue {
  id: string;
  name: string;
  seen: string;
}

export type FieldValue = string | VocabValue;

export interface ScreenDetected {
  screen_id: string | null;
  name: string;
  confidence: number;
  certain: boolean;
  complete: boolean;
  source: "signals" | "vision";
  data: Record<string, FieldValue | Array<Record<string, FieldValue>>>;
  frame_id: number;
}

export interface ProfileInfo {
  id: string;
  name: string;
  screens: Array<{ id: string; description: string }>;
  uses_game_text: boolean;
  install_dir: string | null;
  vocabulary: Record<string, number>;
  sources: Array<{ set: string; origin: string; entries: number }>;
  warnings: string[];
}

export interface ProfileTest {
  screen: string | null;
  confidence: number;
  certain: boolean;
  complete: boolean;
  data: ScreenDetected["data"];
  lines: number;
  candidates: Array<{
    id: string;
    description: string;
    matched: boolean;
    score: number;
    evidence: string[];
    failed: string[];
  }>;
}

export interface BusMessage {
  topic: string;
  data: unknown;
}

export interface AppConfig {
  version: number;
  setup_completed: boolean;
  profile: string;
  auto_detect_game: boolean;
  window_title: string | null;
  game_dirs: Record<string, string>;
  save_dirs: Record<string, string>;
  trusted_plugins: Record<string, string>;
  memory: { enabled: boolean; sessions_in_context: number };
  game_language: string;
  output_language: string;
  capture: {
    idle_fps: number;
    active_fps: number;
    change_threshold: number;
    settle_frames: number;
    max_width: number;
    require_foreground: boolean;
  };
  ocr: { backend: "rapidocr" | "apple-vision" | "windows-ocr" | "none"; min_confidence: number };
  provider: {
    kind: "local-openai";
    base_url: string;
    model: string;
    vision_model: string;
    fast_model: string;
    timeout_seconds: number;
  };
  cloud: Record<"openai" | "anthropic" | "openrouter", { model: string; fast_model: string; vision_model: string }> & {
    allow_images: boolean;
  };
  routing: Record<
    "reasoning" | "fast" | "vision",
    { provider: "local" | "openai" | "anthropic" | "openrouter"; fallback: "none" | "local" | "openai" | "anthropic" | "openrouter" }
  >;
  generation: { answer_tokens: number; thinking: boolean; thinking_tokens: number };
  voice: VoiceConfig;
  output: OutputConfig;
  knowledge: {
    embeddings: "local" | "provider" | "none";
    provider_model: string;
    max_pages: number;
    rate_per_second: number;
    top_k: number;
    auto_refresh: boolean;
    rewrite_queries: boolean;
  };
  gui: { port: number; open_browser: boolean };
}

export type OutputKind = "overlay" | "text" | "voice";

export interface VoiceConfig {
  enabled: boolean;
  input_device: string;
  language: string;
  wake: { enabled: boolean; phrases: string[]; listen_seconds: number };
  push_to_talk: { enabled: boolean; key: string; gamepad_button: string; mode: "hold" | "toggle" };
  stt: { backend: "faster-whisper" | "local-openai"; model: string; wake_model: string; base_url: string };
  tts: { backend: "piper" | "system" | "none"; voice: string; volume: number; rate: number };
  stop_phrases: string[];
  vad_threshold: number;
}

export interface OutputConfig {
  answers: OutputKind[];
  overlay: {
    enabled: boolean;
    corner: "top-left" | "top-right" | "bottom-left" | "bottom-right";
    width: number;
    opacity: number;
    font_scale: number;
    linger_seconds: number;
  };
  proactive: { enabled: boolean; mute_key: string; min_interval_seconds: number };
}

export interface VoiceStateEvent {
  state: "off" | "idle" | "armed" | "recording" | "transcribing" | "error";
  detail: string;
}

export interface AnswerUpdate {
  id: string;
  kind: "question" | "proactive";
  title: string;
  delta: string;
  text: string;
  done: boolean;
  error: string;
  outputs: OutputKind[];
  route: "quick" | "reasoned" | "task";
  model: string;
  sources: Array<{ n: string; title: string; url: string }>;
}

export interface Devices {
  inputs: string[];
  outputs: string[];
  gamepads: string[];
  last_button: string | null;
  gamepad_error: string | null;
  voices: Record<string, string>;
  proactive_muted: boolean;
  error?: string;
}

export interface Status {
  version: string;
  paused: boolean;
  profile: { id: string; name: string } | null;
  capture_backend: string | null;
  ocr_backend: string | null;
  errors: Record<string, string>;
  events: BusMessage[];
}

export interface ProfileSummary {
  id: string;
  name: string;
  description: string;
  bundled: boolean;
  screens: number;
  sources: number;
}

export interface SetupCheck {
  id: string;
  title: string;
  state: "ok" | "warn" | "error" | "off";
  detail: string;
  fix: string;
  command: string;
  settings: string;
  required: boolean;
}

export interface SetupInfo {
  ready: boolean;
  checks: SetupCheck[];
  notes: string[];
}

export interface LocalServer {
  name: string;
  url: string;
  models: string[];
  recommended: string | null;
}

export interface WindowSummary {
  title: string;
  process: string;
  width: number;
  height: number;
}

export interface AskResult {
  text: string;
  model: string;
  elapsed_ms: number;
  usage: { input: number | null; output: number | null };
}

export function connectEvents(onMessage: (message: BusMessage) => void, onState: (open: boolean) => void) {
  let socket: WebSocket | null = null;
  let closed = false;
  let retry = 500;

  const open = () => {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${window.location.host}/api/events`);
    socket.onopen = () => {
      retry = 500;
      socket?.send(JSON.stringify({ token }));
      onState(true);
    };
    socket.onmessage = (event) => onMessage(JSON.parse(String(event.data)) as BusMessage);
    socket.onclose = () => {
      onState(false);
      if (!closed) window.setTimeout(open, (retry = Math.min(retry * 2, 8000)));
    };
  };
  open();
  return () => {
    closed = true;
    socket?.close();
  };
}
