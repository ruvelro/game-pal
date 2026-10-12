"""Prueba de conexión con un modelo, esté donde esté: paso a paso y con qué hacer si algo falla.

La usa el botón «Probar conexión» de la GUI. No guarda nada: prueba lo que el jugador tiene escrito.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from gamepal.checks import server_reply
from gamepal.providers.base import Message, Provider, ProviderError

StepId = Literal["address", "connect", "server", "model", "reply"]

PROMPT = "Responde solo con la palabra OK."
REPLY_TOKENS = 16
# Para el primer uso: un modelo grande puede tardar en cargarse.
REPLY_TIMEOUT_NOTE = "Si es la primera vez, el modelo puede tardar en cargarse: vuelve a probar en un momento."


class Step(BaseModel):
    id: StepId
    ok: bool
    detail: str
    fix: str = ""
    ms: int | None = None


class ConnectionReport(BaseModel):
    ok: bool = False
    target: str
    model: str = ""
    models: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)

    def add(self, step: Step) -> bool:
        self.steps.append(step)
        return step.ok


def address_failed(target: str, error: str) -> ConnectionReport:
    report = ConnectionReport(target=target)
    report.add(
        Step(
            id="address", ok=False, detail=error, fix="Corrige la dirección; por ejemplo http://192.168.1.20:11434/v1."
        )
    )
    return report


def _elapsed(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def check_connection(
    provider: Provider, target: str, model: str, *, remote_hint: bool = False
) -> ConnectionReport:
    """Conecta, pide la lista de modelos, comprueba el elegido y le hace una pregunta mínima."""
    report = ConnectionReport(target=target, model=model)
    started = time.perf_counter()
    try:
        caps = await provider.capabilities()
    except ProviderError as exc:
        reply = server_reply(str(exc))
        if reply is None:
            fix = (
                "Comprueba que ese equipo está encendido y en tu red, que el servidor acepta conexiones de otros "
                "equipos (Ollama: OLLAMA_HOST=0.0.0.0; LM Studio: «Serve on Local Network») y que el cortafuegos "
                "deja pasar el puerto."
                if remote_hint
                else "Comprueba que el servidor está abierto y que la dirección y el puerto son los suyos."
            )
            # «No se pudo contactar con local: ConnectError» → lo técnico, entre paréntesis.
            cause = str(exc).split(": ", 1)[-1]
            detail = f"Nadie responde en esa dirección ({cause})"
            report.add(Step(id="connect", ok=False, detail=detail, fix=fix, ms=_elapsed(started)))
            return report
        status, message = reply
        report.add(Step(id="connect", ok=True, detail="El servidor contesta", ms=_elapsed(started)))
        if status in (401, 403):
            fix = "Escribe la clave que te pide el servidor (o la de tu cuenta, en la nube) y vuelve a probar."
        else:
            fix = (
                "Si tu servidor usa los modelos de otro equipo, comprueba que ese equipo está encendido y "
                "conectado. Si no, puede que en esa dirección haya otro programa."
            )
        report.add(Step(id="server", ok=False, detail=f"Error {status}: {message}", fix=fix))
        return report
    report.add(Step(id="connect", ok=True, detail="El servidor contesta", ms=_elapsed(started)))
    report.models = list(caps.models)
    shown = ", ".join(caps.models[:4]) + ("…" if len(caps.models) > 4 else "")
    report.add(
        Step(
            id="server",
            ok=True,
            detail=f"{len(caps.models)} modelos: {shown}" if caps.models else "No da la lista de modelos",
        )
    )

    if not model:
        report.add(Step(id="model", ok=False, detail="Falta elegir el modelo", fix="Elige uno de la lista."))
        return report
    if caps.models and model not in caps.models:
        report.add(Step(id="model", ok=False, detail=f"El servidor no tiene «{model}»", fix="Elige uno de la lista."))
        return report
    report.add(Step(id="model", ok=True, detail=model))

    started = time.perf_counter()
    try:
        result = await provider.chat([Message("user", PROMPT)], model=model, max_tokens=REPLY_TOKENS)
    except ProviderError as exc:
        report.add(Step(id="reply", ok=False, detail=str(exc), fix=REPLY_TIMEOUT_NOTE, ms=_elapsed(started)))
        return report
    text = result.text.strip().splitlines()[0][:80] if result.text.strip() else "(vacía)"
    report.add(Step(id="reply", ok=True, detail=f"«{text}»", ms=_elapsed(started)))
    report.ok = True
    return report
