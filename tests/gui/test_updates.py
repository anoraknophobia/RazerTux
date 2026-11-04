import pytest

from razertux.config.model import Settings
from razertux.config.store import ConfigStore
from razertux.gui.updates import (
    LATEST_URL, UPDATE_COMMAND, UpdateChecker, UpdateDialog, is_newer, parse_version,
)


def test_parse_and_compare_versions():
    assert parse_version("v0.10.0") == (0, 10, 0)
    assert parse_version("0.9.2") == (0, 9, 2)
    assert parse_version("garbage") is None
    assert is_newer("v0.10.0", "0.9.2")
    assert not is_newer("v0.10.0", "0.10.0")
    assert not is_newer("v0.9.9", "0.10.0")
    assert not is_newer("nonsense", "0.10.0")


class Fetch:
    """Stand-in for the HTTP request: records calls, answers on demand."""

    def __init__(self):
        self.urls, self.callbacks = [], []

    def __call__(self, url, callback):
        self.urls.append(url)
        self.callbacks.append(callback)

    def answer(self, data):
        self.callbacks.pop(0)(data)


@pytest.fixture
def env(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_settings(Settings())
    fetch = Fetch()
    c = UpdateChecker(store, current="0.9.2", fetch=fetch)
    found, notices = [], []
    c.updateAvailable.connect(lambda v, url: found.append((v, url)))
    c.notify.connect(lambda v: notices.append(v))
    return c, store, fetch, found, notices


RELEASE = {"tag_name": "v0.10.0", "html_url": "https://github.com/x/releases/tag/v0.10.0"}


def test_newer_release_is_reported_and_notified_once(env):
    c, store, fetch, found, notices = env
    c.check()
    assert fetch.urls == [LATEST_URL]
    fetch.answer(RELEASE)
    assert found == [("0.10.0", RELEASE["html_url"])] and notices == ["0.10.0"]
    assert c.latest == ("0.10.0", RELEASE["html_url"])
    assert store.load_settings().update_notified == "0.10.0"
    c.check()
    fetch.answer(RELEASE)
    assert notices == ["0.10.0"]  # the tray message only once per version
    assert len(found) == 2  # the menu entry stays


def test_same_or_older_release_and_errors_are_silent(env):
    c, store, fetch, found, notices = env
    for answer in ({"tag_name": "v0.9.2"}, {"tag_name": "v0.9.1"}, None, {"oops": 1}, [1]):
        c.check()
        fetch.answer(answer)
    assert found == [] and notices == [] and c.latest is None


def test_switched_off_means_no_request(env):
    c, store, fetch, found, notices = env
    c.set_enabled(False)
    assert store.load_settings().update_check is False
    c.check()
    assert fetch.urls == []
    c.set_enabled(True)  # switching on checks at once
    assert fetch.urls == [LATEST_URL]


def test_timer_runs_daily(env):
    c, *_ = env
    assert c.timer.interval() == 24 * 3600 * 1000 and c.timer.isActive()


def test_settings_defaults_and_round_trip():
    s = Settings()
    assert s.update_check is True and s.update_notified == ""
    d = Settings(update_check=False, update_notified="0.10.0").to_dict()
    back = Settings.from_dict(d)
    assert (back.update_check, back.update_notified) == (False, "0.10.0")


def test_dialog_shows_command_and_copies_it(qapp):
    d = UpdateDialog("0.10.0", "https://example.org/notes")
    assert d.command.text() == UPDATE_COMMAND and d.command.isReadOnly()
    assert "0.10.0" in d.title.text()
    assert "https://example.org/notes" in d.notes.text()
    d.copy_btn.click()
    assert qapp.clipboard().text() == UPDATE_COMMAND
