import hashlib
from typing import ClassVar

import httpx
import numpy as np
import pytest

from gamepal.voice.models import Artifact, ModelDownloadError, ModelStore, piper_artifacts
from gamepal.voice.phrases import PhraseMatcher
from gamepal.voice.tts import SentenceBuffer, speakable
from gamepal.voice.vad import FRAME, EnergyVad, Segmenter


@pytest.mark.parametrize(
    ("text", "rest"),
    [
        ("Oye compañero, ¿qué bendición cojo?", "qué bendición cojo?"),
        ("oye compañeros que hago", "que hago"),  # error de transcripción
        ("Eh, oye compañero", ""),
        ("Hey pal, what now?", "what now?"),
        # Lo que Whisper entiende a veces: la palabra larga casi exacta y algo corto delante.
        ("Puede compañero.", ""),
        ("Hoy es compañero, ¿qué planto?", "qué planto?"),
    ],
)
def test_wake_phrase_at_start(text: str, rest: str) -> None:
    hit = PhraseMatcher(["oye compañero", "hey pal"]).match_start(text)
    assert hit is not None
    assert hit.rest == rest


@pytest.mark.parametrize("text", ["Le dije a mi compañero que oye", "Hola qué tal", "pal"])
def test_wake_phrase_not_elsewhere(text: str) -> None:
    assert PhraseMatcher(["oye compañero", "hey pal"]).match_start(text) is None


def test_stop_phrases_only_when_they_are_all_that_was_said() -> None:
    matcher = PhraseMatcher(["gracias", "para"])
    assert matcher.whole("Vale, gracias.") == "gracias"
    assert matcher.whole("Para ya.") == "para"
    assert matcher.whole("paragüero") is None
    # La propia voz de game-pal, oída por el micrófono, no debe cortarla.
    assert matcher.whole("Úsala para invierno y luego vuelve a casa.") is None
    assert matcher.whole("para que termine la semana") is None


def test_segmenter_cuts_on_silence() -> None:
    seg = Segmenter(threshold=0.5, min_speech=0.1, hangover=0.2, pre_roll=0.1)
    frame = np.zeros(FRAME, dtype=np.float32)
    outputs = [seg.feed(frame, p) for p in [0.0] * 5 + [0.9] * 10 + [0.0] * 10]
    utterances = [u for u in outputs if u is not None]
    assert len(utterances) == 1
    # Pre-roll (3 frames) + voz (10); el silencio final no se incluye.
    assert len(utterances[0].audio) == 13 * FRAME


def test_segmenter_ignores_clicks_and_supports_push_to_talk() -> None:
    seg = Segmenter(min_speech=0.25, hangover=0.1)
    frame = np.zeros(FRAME, dtype=np.float32)
    assert all(seg.feed(frame, p) is None for p in [0.9, 0.0, 0.0, 0.0, 0.0])
    seg.force_start()
    for _ in range(20):
        assert seg.feed(frame, 0.0) is None  # con push-to-talk el silencio no corta
    utterance = seg.force_end()
    assert utterance is not None
    assert utterance.forced


def test_energy_vad_adapts_to_noise() -> None:
    vad = EnergyVad()
    rng = np.random.default_rng(1)
    noise = [(rng.standard_normal(FRAME) * 0.002).astype(np.float32) for _ in range(30)]
    assert max(vad.probability(f) for f in noise) < 0.5
    loud = (np.sin(np.linspace(0, 200, FRAME)) * 0.3).astype(np.float32)
    assert vad.probability(loud) > 0.9


def test_sentence_buffer_streams_sentences() -> None:
    buffer = SentenceBuffer()
    assert buffer.feed("Coge la espada") == []
    assert buffer.feed(". Es rara y ") == ["Coge la espada."]
    assert buffer.feed("fuerte.") == []
    assert buffer.flush() == ["Es rara y fuerte."]


def test_speakable_drops_links_and_markup() -> None:
    text = "**Coge** [la espada](https://wiki.example/espada) 【3†fuente】 ya: https://x.y/z"
    assert speakable(text) == "Coge la espada ya:"


def test_piper_ids_are_validated() -> None:
    model, meta = piper_artifacts("es_ES-davefx-medium")
    assert model.url.endswith("/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx")
    assert meta.name == "piper/es_ES-davefx-medium.onnx.json"
    with pytest.raises(ModelDownloadError):
        piper_artifacts("../../etc/passwd-x-y")


def _store(tmp_path, body: bytes) -> ModelStore:  # type: ignore[no-untyped-def]
    return ModelStore(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))


def test_download_verifies_hash(tmp_path) -> None:  # type: ignore[no-untyped-def]
    body = b"x" * 2048
    good = Artifact("m.bin", "https://huggingface.co/m.bin", hashlib.sha256(body).hexdigest())
    assert _store(tmp_path, body).ensure(good).read_bytes() == body
    bad = Artifact("n.bin", "https://huggingface.co/n.bin", "0" * 64)
    with pytest.raises(ModelDownloadError, match="hash"):
        _store(tmp_path, body).ensure(bad)
    assert not (tmp_path / "n.bin").exists()
    assert not list(tmp_path.glob(".dl-*"))


def test_download_rejects_untrusted_hosts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ModelDownloadError, match="no permitido"):
        _store(tmp_path, b"x").ensure(Artifact("a", "https://evil.example/a"))
    with pytest.raises(ModelDownloadError, match="no permitido"):
        _store(tmp_path, b"x").ensure(Artifact("a", "http://huggingface.co/a"))


class FakeStream:
    opened: ClassVar[list["FakeStream"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.written = 0
        self.aborted = False
        FakeStream.opened.append(self)

    def start(self) -> None:
        return None

    def write(self, data: np.ndarray) -> None:
        self.written += len(data)

    def stop(self) -> None:
        return None

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        return None


def test_speaker_plays_sentences_through_one_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from gamepal.voice.audio import Speaker

    fake = types.ModuleType("sounddevice")
    fake.OutputStream = FakeStream  # type: ignore[attr-defined]
    fake.query_devices = lambda: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    FakeStream.opened.clear()
    speaker = Speaker()
    sentence = np.zeros(22050 // 2, dtype=np.float32)
    for _ in range(3):
        speaker.play(sentence, 22050)
    assert speaker.wait_idle(5)
    # Tres frases, un solo flujo: abrir uno por frase dejaba cortes entre ellas.
    assert len(FakeStream.opened) == 1
    assert FakeStream.opened[0].kwargs["latency"] == "high"
    assert FakeStream.opened[0].written >= 3 * len(sentence)
    speaker.play(np.zeros(22050 * 5, dtype=np.float32), 22050)
    speaker.stop()
    assert speaker.wait_idle(5)
