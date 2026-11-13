import json

from tradingagent import republish
from tradingagent.crypto_box import Box


def _event(tmp_path, files):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({"commits": [{"added": [], "modified": files, "removed": []}]}))
    return str(p)


def test_only_app_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert republish.only_app_changed(_event(tmp_path, ["app/app.js", "app/version.json"]))
    assert not republish.only_app_changed(_event(tmp_path, ["app/app.js", "src/tradingagent/export.py"]))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    assert not republish.only_app_changed(_event(tmp_path, ["app/app.js"]))


class _Resp:
    def __init__(self, body):
        self.status_code, self.content = (200, body) if body is not None else (404, b"")


class _Http:
    def __init__(self, files):
        self.files = files

    def get(self, url, timeout=None):
        return _Resp(self.files.get(url.rsplit("data/", 1)[1]))


def test_fetch_published_copies_all_files(tmp_path):
    box = Box("ein-langes-passwort-123")
    index = box.seal({"advisor": "advisor.json", "instruments": [{"id": "SAP", "file": "s/abc.json"}]})
    index["kdf"] = box.kdf_header()
    files = {"index.json": json.dumps(index).encode(), "s/abc.json": b'{"x":1}', "advisor.json": b'{"y":2}'}
    out = tmp_path / "data"
    n = republish.fetch_published(out, "ein-langes-passwort-123", session=_Http(files), base="https://x/data/")
    assert n == 3 and (out / "s" / "abc.json").read_bytes() == b'{"x":1}' and (out / "advisor.json").exists()
    files.pop("s/abc.json")
    assert republish.fetch_published(tmp_path / "d2", "ein-langes-passwort-123", session=_Http(files), base="https://x/data/") == 0
