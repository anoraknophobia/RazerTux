import pytest

from razertux import i18n


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


@pytest.fixture(autouse=True)
def fake_home(monkeypatch, tmp_path_factory):
    """No test may read or write the real ~/.config (naga-config or razertux)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home
