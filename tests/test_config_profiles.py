from pathlib import Path

import pytest
from pydantic import ValidationError

from gamepal.config import AppConfig, load_config, save_config
from gamepal.profiles import GameProfile, ProfileError, ProfileStore, load_profile


def test_config_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config = AppConfig(profile="generic", output_language="fr")
    save_config(config, path)
    assert load_config(path) == config
    assert not list(tmp_path.glob(".config-*"))


def test_missing_config_gives_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.yaml") == AppConfig()


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("profile: generic\napi_key: secreto\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)


def test_bundled_generic_profile_is_valid(repo_profiles: Path) -> None:
    loaded = load_profile(repo_profiles / "generic", bundled=True)
    assert loaded.profile.id == "generic"
    assert "{output_language}" in (loaded.read_prompt("advisor") or "")


def _write(folder: Path, body: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "profile.yaml").write_text(body, encoding="utf-8")
    return folder


def test_profile_id_must_match_folder(tmp_path: Path) -> None:
    folder = _write(tmp_path / "otro", "id: mi-juego\nname: Mi juego\n")
    with pytest.raises(ProfileError, match="coincidir"):
        load_profile(folder)


@pytest.mark.parametrize(
    "body",
    [
        "id: Mal_Id\nname: x\n",
        "id: juego\nname: x\ngame: {process: ['C:/juegos/x.exe']}\n",
        "id: juego\nname: x\nprompts: {advisor: ../../etc/passwd}\n",
        "id: juego\nname: x\nknowledge: {sources: [{url: 'file:///etc/passwd'}]}\n",
        "id: juego\nname: x\nscreens: [{id: menu}, {id: menu}]\n",
        "id: juego\nname: x\nzona: {x: 0.2}\n",
    ],
)
def test_invalid_profiles_are_rejected(tmp_path: Path, body: str) -> None:
    folder = _write(tmp_path / "juego", body)
    if "Mal_Id" in body:
        (tmp_path / "juego").rename(tmp_path / "Mal_Id")
        folder = tmp_path / "Mal_Id"
    with pytest.raises(ProfileError):
        load_profile(folder)


def test_user_profile_overrides_bundled(tmp_path: Path, repo_profiles: Path) -> None:
    user = tmp_path / "user"
    _write(user / "generic", "id: generic\nname: Mi genérico\n")
    _write(user / "roto", "id: roto\nname: [\n")
    profiles, errors = ProfileStore(repo_profiles, user).discover()
    assert profiles["generic"].profile.name == "Mi genérico"
    assert not profiles["generic"].bundled
    assert "roto (usuario)" in errors


def test_schema_has_no_coordinates() -> None:
    schema = GameProfile.model_json_schema()
    assert "Screen" in schema["$defs"]


def test_signals_are_validated(tmp_path: Path) -> None:
    folder = _write(
        tmp_path / "juego",
        "id: juego\nname: x\nscreens:\n  - id: menu\n    signals: {text_matches: {pattern: '(sin cerrar'}}\n",
    )
    with pytest.raises(ProfileError, match="regular"):
        load_profile(folder)


def test_lint_reports_undeclared_vocabulary(tmp_path: Path) -> None:
    from gamepal.profiles.lint import lint

    folder = _write(
        tmp_path / "juego",
        "id: juego\nname: x\nscreens:\n  - id: menu\n    signals: {text_matches: {vocabulary: fantasma}}\n",
    )
    assert lint(load_profile(folder)) == ["La pantalla 'menu' usa el vocabulario 'fantasma', que no está declarado"]


def test_bundled_profiles_have_no_warnings(repo_profiles: Path) -> None:
    from gamepal.profiles.lint import lint

    for name in ("generic", "hades-2", "stardew-valley"):
        assert lint(load_profile(repo_profiles / name, bundled=True)) == []


def test_committed_schema_is_up_to_date(repo_profiles: Path) -> None:
    import json

    committed = json.loads((repo_profiles / "profile.schema.json").read_text(encoding="utf-8"))
    assert committed == GameProfile.model_json_schema(), (
        "Regenera con: uv run game-pal schema --out profiles/profile.schema.json"
    )


def test_new_profile_scaffold(tmp_path: Path, repo_profiles: Path) -> None:
    from gamepal.profiles.lint import lint
    from gamepal.profiles.loader import ProfileError
    from gamepal.profiles.scaffold import create_profile

    folder = create_profile(tmp_path, "hollow-knight", "Hollow Knight", process="hollow_knight",
                            schema=repo_profiles / "profile.schema.json")  # fmt: skip
    loaded = load_profile(folder)
    assert loaded.profile.name == "Hollow Knight"
    assert loaded.profile.game.process == ["hollow_knight"]
    assert lint(loaded) == []
    assert "Hollow Knight" in (loaded.read_prompt("advisor") or "")
    with pytest.raises(ProfileError, match="Ya existe"):
        create_profile(tmp_path, "hollow-knight", "Otra vez")
    with pytest.raises(ProfileError, match="minúsculas"):
        create_profile(tmp_path, "Mal Id", "x")
    # Un nombre con comillas o dos puntos no rompe el YAML.
    tricky = create_profile(tmp_path, "tricky", 'Juego: "el regreso"')
    assert load_profile(tricky).profile.name == 'Juego: "el regreso"'
