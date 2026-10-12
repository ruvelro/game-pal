import { useEffect, useState } from "react";
import { t } from "../i18n";

/** Convierte una pulsación del navegador a la sintaxis de atajos de pynput: «<ctrl>+<alt>+p». */
function toCombo(event: KeyboardEvent): string | null {
  const modifiers = ["Control", "Alt", "Shift", "Meta"];
  if (modifiers.includes(event.key)) return null;
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("<ctrl>");
  if (event.altKey) parts.push("<alt>");
  if (event.shiftKey) parts.push("<shift>");
  if (event.metaKey) parts.push("<cmd>");
  const code = event.code;
  let key: string;
  if (/^F\d{1,2}$/.test(event.key)) key = `<${event.key.toLowerCase()}>`;
  else if (code === "Space") key = "<space>";
  else if (code.startsWith("Key")) key = code.slice(3).toLowerCase();
  else if (code.startsWith("Digit")) key = code.slice(5);
  else if (event.key.length === 1) key = event.key.toLowerCase();
  else key = `<${event.key.toLowerCase()}>`;
  parts.push(key);
  return parts.join("+");
}

export function ComboInput({ value, onChange }: { value: string; onChange: (combo: string) => void }) {
  const [recording, setRecording] = useState(false);
  useEffect(() => {
    if (!recording) return;
    const handler = (event: KeyboardEvent) => {
      event.preventDefault();
      const combo = toCombo(event);
      if (combo) {
        onChange(combo);
        setRecording(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [recording, onChange]);
  return (
    <div className="row">
      <input value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
      <button onClick={() => setRecording(true)} className={recording ? "primary" : ""}>
        {recording ? t("voice.ptt.recording") : t("voice.ptt.record")}
      </button>
    </div>
  );
}
