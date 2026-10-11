"""Respuestas con datos exactos: calendario, tablas del perfil, fichas de la wiki y texto limpio."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from gamepal.assistant import Assistant, plain_text, question_terms
from gamepal.bus import EventBus
from gamepal.checks import identify
from gamepal.config import AppConfig, load_config
from gamepal.events import AnswerUpdate, QuestionAsked
from gamepal.knowledge.lookup import WikiLookup, WikiPage, candidates
from gamepal.knowledge.service import KnowledgeService
from gamepal.knowledge.store import KnowledgeStore
from gamepal.profiles import LoadedProfile, ProfileStore, load_profile
from gamepal.providers import AnswerCutShort, Message, ProviderError
from gamepal.reference import Reference, Today, words
from gamepal.voice import tts

SPRING_18 = Today("spring", 18, 3)


@pytest.fixture
def stardew(repo_profiles: Path, tmp_path: Path) -> LoadedProfile:
    return ProfileStore(repo_profiles, tmp_path).get("stardew-valley")


@pytest.fixture
def reference(stardew: LoadedProfile) -> Reference:
    return Reference.from_profile(stardew)


# Calendario y tablas ---------------------------------------------------------------------------


def test_calendar_counts_the_days_left(reference: Reference) -> None:
    text = reference.calendar(SPRING_18, "es")
    assert "Hoy es el día 18 de primavera, año 3" in text
    assert "quedan 10 (del día 19 al 28)" in text
    assert "Después empieza verano" in text
    assert "último" in reference.calendar(Today("spring", 28, 3), "es")
    assert reference.calendar(None, "es") == ""


def test_crops_are_found_by_name_in_any_language_and_plural(reference: Reference) -> None:
    def rows(text: str) -> list[str]:
        return [e["names"]["en"] for _, entries in reference.find(text, SPRING_18) for e in entries]

    assert rows("¿Cuánto tardan las chirivías?") == ["Parsnip"]
    assert rows("¿Dan dinero las judías verdes?") == ["Green Bean"]
    assert "Parsnip" in rows("Parsnip Seeds")  # «seeds» además trae los cultivos de la estación
    assert rows("¿Qué hago con la katana de lava?") == []


def test_a_general_question_brings_the_crops_of_the_season(reference: Reference) -> None:
    found = reference.find("¿Qué me recomiendas plantar mañana?", SPRING_18)
    names = {e["names"]["en"] for _, entries in found for e in entries}
    assert {"Parsnip", "Potato", "Kale"} <= names
    # Sin nombrarlas, fuera las que no se pueden comprar (fresa: el festival ya pasó; zanahoria y fruta
    # milenaria: no se venden en tiendas).
    assert not names & {"Strawberry", "Carrot", "Ancient Fruit"}
    assert "Melon" not in names  # de verano
    assert "Cauliflower" not in names  # 12 días: ya no llega, y no se nombró
    named = reference.find("¿Me da tiempo a plantar coliflores?", SPRING_18)
    assert "Cauliflower" in {e["names"]["en"] for _, entries in named for e in entries}


def test_the_calendar_lists_festivals_and_the_next_one(reference: Reference) -> None:
    text = reference.calendar(SPRING_18, "es")
    assert "Festival del huevo (día 13, ya pasó)" in text
    assert "Festival del Desierto (días 15 a 17, ya pasó)" in text
    assert "Danza floral (día 24, dentro de 6 días)" in text
    assert "La próxima fiesta es Danza floral: dentro de 6 días (día 24 de primavera)" in text
    assert "(días 15 a 17, es hoy)" in reference.calendar(Today("spring", 16, 1), "es")
    assert "Danza floral (día 24, mañana)" in reference.calendar(Today("spring", 23, 1), "es")
    # Pasadas todas las de la estación, la primera de la siguiente.
    late = reference.calendar(Today("spring", 26, 1), "es")
    assert "La próxima fiesta es Luau: dentro de 13 días (día 11 de verano)" in late
    winter = reference.calendar(Today("winter", 28, 1), "en")
    assert "The next festival is Egg Festival: in 13 days (day 13 of spring)" in winter


def test_seeds_sold_only_at_a_past_festival_are_flagged(reference: Reference) -> None:
    lines = reference.render("fresa chirivía", SPRING_18, "es").splitlines()
    strawberry = next(line for line in lines if line.startswith("- Fresa"))
    parsnip = next(line for line in lines if line.startswith("- Chirivía"))
    assert "ahora no se vende en ninguna tienda" in strawberry
    assert "ahora no se vende" not in parsnip
    assert "no se vende en tiendas" in reference.render("zanahoria", SPRING_18, "es")  # sale de semillas silvestres
    early = reference.render("fresa", Today("spring", 5, 1), "es")
    assert "solo el día 13 de primavera" in early
    assert "ahora no se vende" not in early


def test_rows_carry_the_dates_already_counted(reference: Reference) -> None:
    text = reference.render("chirivía coliflor judía verde maíz fresa", SPRING_18, "es")
    lines = {line.split(" · ")[0]: line for line in text.splitlines() if line.startswith("- ")}
    assert (
        "plantada hoy (día 18): sí llega, se cosecha el día 22; "
        "plantada mañana (día 19): sí llega, se cosecha el día 23" in lines["- Chirivía (Parsnip)"]
    )
    assert "plantada hoy (día 18): no llega" in lines["- Coliflor (Cauliflower)"]
    beans = lines["- Judía verde (Green Bean)"]
    assert "plantada hoy (día 18): sí llega, primera cosecha el día 28, 1 sola cosecha esta estación" in beans
    assert "plantada mañana (día 19): no llega" in beans
    assert "Festival del Huevo 100 g (ya pasó este año: fue el día 13 de primavera)" in lines["- Fresa (Strawberry)"]
    assert "Pierre 20 g" in lines["- Chirivía (Parsnip)"]
    summer = reference.render("maíz", Today("summer", 20, 1), "es")
    assert "sigue creciendo en otoño" in summer  # el maíz es de verano y otoño
    assert "Días base" in text


def test_words_drop_accents_and_plurals() -> None:
    assert words("Chirivías Verdes") == words("chirivía verde") == ("chirivia", "verd")
    assert words("beans") == words("bean")
    assert words("Blueberries") == words("blueberry")
    assert words("melones") == words("melón")


# Fichas de la wiki -----------------------------------------------------------------------------


class FakeFetcher:
    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    async def get_json(self, url: str) -> Any:
        self.calls.append(url)
        for key, value in self.replies.items():
            if key in url:
                return value
        raise AssertionError(url)

    async def aclose(self) -> None:
        return None


ES = "https://es.wiki.example/api.php"
EN = "https://en.wiki.example/api.php"


async def test_lookup_finds_the_item_on_screen_in_the_game_language() -> None:
    fetcher = FakeFetcher({
        "action=query": {"query": {"pages": [
            {"title": "Calzado", "fullurl": "https://es.wiki.example/Calzado"},
            {"title": "Botas térmicas", "fullurl": "https://es.wiki.example/Botas_térmicas"},
            {"title": "Jue. 18", "missing": True},
        ]}},
        "Botas+t%C3%A9rmicas": {"parse": {"text": "<p>Las <b>Botas térmicas</b> son un calzado. Defensa +1.</p>"}},
        "Calzado": {"parse": {"text": "<p>El calzado da defensa.</p>"}},
    })  # fmt: skip
    lookup = WikiLookup([("en", EN), ("es", ES)], fetcher=fetcher)  # type: ignore[arg-type]
    names = candidates(["Botas térmicas", "Calzado", "Jue. 18", "155216"])
    assert names == ["Botas térmicas", "Calzado"]  # «Jue. 18» y «155216» no parecen nombres
    pages = await lookup.find(names, "es")
    assert [p.title for p in pages] == ["Botas térmicas", "Calzado"]
    assert pages[0].language == "es"
    assert "Defensa +1" in pages[0].text
    assert fetcher.calls[0].startswith(ES)  # primero la wiki del idioma del juego
    calls = len(fetcher.calls)
    await lookup.find(names, "es")
    assert len(fetcher.calls) == calls  # ya en caché


async def test_save_names_are_translated_but_not_through_redirects() -> None:
    fetcher = FakeFetcher({"prop=langlinks": {"query": {
        "redirects": [{"from": "Gold Pickaxe", "to": "Pickaxes"}],
        "pages": [
            {"title": "Trash", "langlinks": [{"lang": "es", "title": "Basura"}]},
            {"title": "Pickaxes", "langlinks": [{"lang": "es", "title": "Picos"}]},
            {"title": "Pineapple", "langlinks": [{"lang": "es", "title": "Piña (fruta)"}]},
        ],
    }}})  # fmt: skip
    lookup = WikiLookup([("es", ES), ("en", EN)], fetcher=fetcher)  # type: ignore[arg-type]
    names = await lookup.translate(["Trash", "Gold Pickaxe", "Pineapple"], "es")
    assert names == {"Trash": "Basura", "Pineapple": "Piña"}
    assert await lookup.translate(["Trash"], "en") == {}


def test_question_terms_skip_filler_words() -> None:
    terms = question_terms("¿Para qué sirve la katana de lava?")
    assert "katana de lava" in terms
    assert "para qué" not in terms


# Asistente ---------------------------------------------------------------------------------------


class Recorder:
    """Proveedor falso: guarda los mensajes y responde lo que se le diga."""

    model = "falso"

    def __init__(self, reply: str, cut: bool = False) -> None:
        self.reply, self.cut = reply, cut
        self.messages: list[Message] = []

    async def chat_stream(self, messages: list[Message], **_: Any):  # type: ignore[no-untyped-def]
        self.messages = messages
        for word in self.reply.split(" "):
            yield word + " "
        if self.cut:
            raise AnswerCutShort("falso: respuesta cortada")

    async def chat(self, *_: Any, **__: Any) -> Any:
        raise ProviderError("sin palabras clave en este test")


async def _answer(provider: Recorder, stardew: LoadedProfile, reference: Reference, text: str) -> AnswerUpdate:
    bus = EventBus()

    async def lookup(names: list[str], language: str) -> list[WikiPage]:
        return [WikiPage("Botas térmicas", "https://es.wiki/Botas", "Calzado. Defensa +1.", "es")]

    assistant = Assistant(
        bus, config=lambda: AppConfig(), provider=lambda: provider, profile=lambda: stardew,  # type: ignore[arg-type,return-value]
        screen=lambda: None, read=lambda: None, reference=lambda: reference, today=lambda: SPRING_18, lookup=lookup,
    )  # fmt: skip
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    try:
        async with bus.subscribe({"assistant.answer"}) as sub:
            bus.publish(QuestionAsked(id="q", text=text, source="text"))
            while True:
                event = await asyncio.wait_for(sub.get(), 5)
                assert isinstance(event, AnswerUpdate)
                if event.done:
                    return event
    finally:
        runner.cancel()


async def test_answers_get_exact_facts_and_rules(stardew: LoadedProfile, reference: Reference) -> None:
    provider = Recorder("Las **chirivías** tardan 4 días [2].")
    event = await _answer(provider, stardew, reference, "¿Cuánto tardan las chirivías?")
    system, question = provider.messages[0].text, provider.messages[-1].text
    assert "No des días, fechas, precios ni cantidades" in system
    assert "sin Markdown" in system
    assert "quedan 10 (del día 19 al 28)" in question
    assert "Chirivía (Parsnip) · primavera · 4 días" in question
    assert "Ficha «Botas térmicas»" in question
    # Sin Markdown y solo con la fuente citada.
    assert event.text == "Las chirivías tardan 4 días [2]."
    assert event.sources == []  # la [2] no existe: solo hay una ficha, la [1]


async def test_a_cut_answer_says_so(stardew: LoadedProfile, reference: Reference) -> None:
    event = await _answer(Recorder("Planta chirivías porque", cut=True), stardew, reference, "¿Qué planto?")
    assert event.text.startswith("Planta chirivías porque…")
    assert "Respuesta cortada" in event.text
    assert not event.error


def test_plain_text_removes_markdown() -> None:
    assert plain_text("## Plan\n**Patatas**: 6 días.\n* Ajo\n- Col") == "Plan\nPatatas: 6 días.\n• Ajo\n• Col"
    assert plain_text("Fresas a 4 días [1]") == "Fresas a 4 días [1]"


# Arreglos de la pasada con Stardew ---------------------------------------------------------------


def test_old_configs_stop_thinking(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("version: 1\ngeneration:\n  thinking: true\n", encoding="utf-8")
    config = load_config(path)
    assert config.generation.thinking is False
    assert config.version == 2
    assert AppConfig().generation.thinking is False
    path.write_text("version: 2\ngeneration:\n  thinking: true\n", encoding="utf-8")
    assert load_config(path).generation.thinking is True  # elegido a mano después: se respeta


async def test_interrupted_downloads_are_resumed(tmp_path: Path, repo_profiles: Path) -> None:
    profile = load_profile(repo_profiles / "generic", bundled=True)
    store = KnowledgeStore(tmp_path / "data" / "knowledge" / "generic.sqlite")
    source = store.upsert_source("url", "https://wiki.example/", "user", {})
    store.set_status(source.id, "running")  # game-pal se cerró a mitad
    store.close()
    config = AppConfig.model_validate({"knowledge": {"embeddings": "none"}})
    service = KnowledgeService(
        EventBus(), tmp_path / "data", tmp_path / "cache", config=lambda: config, provider=lambda: None
    )
    service._work = lambda: asyncio.sleep(3600)  # type: ignore[method-assign]  # sin descargar de verdad
    await service.open(profile)
    assert service.store is not None
    assert service.store.source(source.id).status == "queued"  # type: ignore[union-attr]
    await service.close()


async def test_servers_are_named_by_what_they_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 11434 and request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.2"})
        if request.url.port == 1234 and request.url.path == "/api/v0/models":
            return httpx.Response(200, json={"data": [{"id": "m", "state": "loaded"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    assert await identify("http://127.0.0.1:11434/v1", transport) == "Ollama"
    assert await identify("http://127.0.0.1:1234/v1", transport) == "LM Studio"
    assert await identify("http://127.0.0.1:8080/v1", transport) == "el servidor del puerto 8080"


def test_espeak_gets_a_short_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import piper.phonemize_espeak as espeak

    deep = tmp_path / ("x" * 160) / "espeak-ng-data"
    deep.mkdir(parents=True)
    (deep / "phontab").write_bytes(b"")
    monkeypatch.setattr(espeak, "ESPEAK_DATA_DIR", deep)
    monkeypatch.setattr(tts.tempfile, "gettempdir", lambda: str(tmp_path))
    short = tts.espeak_data_dir()
    assert short is not None
    assert len(str(short)) <= tts.MAX_ESPEAK_PATH
    assert (short / "phontab").is_file()


def test_a_broken_piper_is_detected_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> Any:
        calls.append(command)
        return type("Done", (), {"returncode": 1, "stderr": b"Error processing file phontab"})()

    monkeypatch.setattr(tts.subprocess, "run", run)
    assert tts.piper_works(tmp_path, tmp_path) is False
    assert tts.piper_works(tmp_path, tmp_path) is False
    assert len(calls) == 1  # se recuerda


def test_overlay_closes_when_game_pal_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    from gamepal.overlay import __main__ as overlay

    parents = iter([100, 100, 1])  # al morir game-pal, el overlay pasa a depender de launchd
    monkeypatch.setattr(overlay.os, "getppid", lambda: next(parents))

    def leave(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(overlay.os, "_exit", leave)
    if os.name == "nt":
        pytest.skip("en Windows se comprueba con OpenProcess")
    with pytest.raises(SystemExit):
        overlay.watch_parent(os.getpid(), interval=0)


def test_crop_table_is_valid_json_friendly(stardew: LoadedProfile) -> None:
    reference = Reference.from_profile(stardew)
    crops = reference.tables[0].entries
    assert len(crops) >= 40
    parsnip = next(c for c in crops if c["names"]["en"] == "Parsnip")
    assert (parsnip["growth_days"], parsnip["names"]["es"]) == (4, "Chirivía")
    json.dumps(crops)  # solo datos simples
