import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { LiveState } from "../store";

/** La última captura de la ventana del juego, refrescada con cada frame nuevo. */
export function Preview({ live, paused }: { live: LiveState; paused: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  const loading = useRef(false);
  const frameId = live.frame?.frame_id;

  useEffect(() => {
    if (frameId === undefined || paused || loading.current) return;
    loading.current = true;
    api
      .get<Blob>("/api/frame.jpg")
      .then((blob) =>
        setSrc((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return URL.createObjectURL(blob);
        }),
      )
      .catch(() => undefined)
      .finally(() => {
        loading.current = false;
      });
  }, [frameId, paused]);

  return (
    <div className="preview">
      {src ? <img src={src} alt={t("home.preview")} /> : <p className="muted">{t("home.preview.empty")}</p>}
      {live.window && (
        <div className="preview-meta">
          <span>{live.window.title || live.window.process}</span>
          <span>
            {live.window.width}×{live.window.height}
          </span>
        </div>
      )}
    </div>
  );
}
