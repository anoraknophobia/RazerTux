import pytest

from naga_config import i18n


@pytest.fixture(autouse=True)
def german_locale(monkeypatch):
    """Tests expect the German source texts: pin the system locale to German and
    reset the process language (the daemon/GUI set it from settings.json)."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    i18n.set_language("de")
    yield
    i18n.set_language("de")
