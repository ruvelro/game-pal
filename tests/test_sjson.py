import pytest

from gamepal.profiles import sjson


def test_parses_supergiant_style_text() -> None:
    text = """
    /* cabecera */
    Texts = [
      {
        Id = "ZeusWeaponBoon" // comentario
        DisplayName = "Golpe \\"celestial\\""
        Values = [ 1, 2.5, true, nil ]
      }
      { Id = Plain, DisplayName = 'Simple' }
    ]
    """
    data = sjson.loads(text)
    first, second = data["Texts"]
    assert first["DisplayName"] == 'Golpe "celestial"'
    assert first["Values"] == [1, 2.5, True, None]
    assert second == {"Id": "Plain", "DisplayName": "Simple"}


def test_parses_braced_documents_and_triple_strings() -> None:
    assert sjson.loads('{ A = """dos\nlíneas""" B: 3 }') == {"A": "dos\nlíneas", "B": 3}


@pytest.mark.parametrize("bad", ["{ A = ", "{ A 1 }", "[1, 2", "{ A = 'x }", "/* sin cerrar", "{ A = 1 } }"])
def test_rejects_malformed_input(bad: str) -> None:
    with pytest.raises(sjson.SjsonError):
        sjson.loads(bad)


def test_depth_is_limited() -> None:
    with pytest.raises(sjson.SjsonError, match="Anidamiento"):
        sjson.loads("[" * 100 + "]" * 100)
