import { useCallback, useEffect, useState } from "react";
import {
  api,
  connectEvents,
  type AppConfig,
  type BusMessage,
  type Component,
  type EvidenceSnapshot,
  type FrameCaptured,
  type KnowledgeProgress,
  type AnswerUpdate,
  type ScreenDetected,
  type ScreenRead,
  type Status,
  type StatusEvent,
  type VoiceStateEvent,
  type WindowSeen,
} from "./api";

export interface LiveState {
  connected: boolean;
  statuses: Partial<Record<Component, StatusEvent>>;
  window: WindowSeen | null;
  frame: FrameCaptured | null;
  read: ScreenRead | null;
  screen: ScreenDetected | null;
  voice: VoiceStateEvent | null;
  speaking: boolean;
  answers: AnswerUpdate[];
  knowledge: Record<number, KnowledgeProgress>;
  evidence: EvidenceSnapshot | null;
  activity: number;
}

const initial: LiveState = {
  connected: false,
  statuses: {},
  window: null,
  frame: null,
  read: null,
  screen: null,
  voice: null,
  speaking: false,
  answers: [],
  knowledge: {},
  evidence: null,
  activity: 0,
};

const MAX_ANSWERS = 6;

function reduce(state: LiveState, message: BusMessage): LiveState {
  switch (message.topic) {
    case "status": {
      const event = message.data as StatusEvent;
      return { ...state, statuses: { ...state.statuses, [event.component]: event } };
    }
    case "capture.window":
      return { ...state, window: message.data as WindowSeen };
    case "capture.frame":
      return { ...state, frame: message.data as FrameCaptured };
    case "perception.text":
      return { ...state, read: message.data as ScreenRead };
    case "perception.screen": {
      const screen = message.data as ScreenDetected;
      return { ...state, screen: screen.screen_id ? screen : null };
    }
    case "knowledge.progress": {
      const progress = message.data as KnowledgeProgress;
      return { ...state, knowledge: { ...state.knowledge, [progress.source_id]: progress } };
    }
    case "evidence":
      return { ...state, evidence: (message.data as { snapshot: EvidenceSnapshot }).snapshot };
    case "activity":
      return { ...state, activity: state.activity + 1 };
    case "voice.state":
      return { ...state, voice: message.data as VoiceStateEvent };
    case "voice.speaking":
      return { ...state, speaking: Boolean((message.data as { speaking: boolean }).speaking) };
    case "assistant.answer": {
      const update = message.data as AnswerUpdate;
      const others = state.answers.filter((a) => a.id !== update.id);
      return { ...state, answers: [update, ...others].slice(0, MAX_ANSWERS) };
    }
    default:
      return state;
  }
}

export function useLive(): LiveState {
  const [state, setState] = useState(initial);
  useEffect(
    () =>
      connectEvents(
        (message) => setState((current) => reduce(current, message)),
        (connected) => setState((current) => ({ ...current, connected })),
      ),
    [],
  );
  return state;
}

export function useStatus(intervalMs = 3000) {
  const [status, setStatus] = useState<Status | null>(null);
  const refresh = useCallback(async () => {
    try {
      setStatus(await api.get<Status>("/api/status"));
    } catch {
      /* el WebSocket ya refleja la desconexión */
    }
  }, []);
  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs]);
  return { status, refresh };
}

export function useConfig() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .get<AppConfig>("/api/config")
      .then(setConfig)
      .catch((e: Error) => setError(e.message));
  }, []);

  const save = useCallback(async (next: AppConfig) => {
    setError(null);
    setSaved(false);
    try {
      setConfig(await api.put<AppConfig>("/api/config", next));
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }, []);

  return { config, setConfig, save, error, saved };
}
