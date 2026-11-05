"""A crash the UI reports lands in the runtime dir as one JSON line."""
import json

from routes import client_errors


def test_report_appends_one_line_per_error(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path))
    body = client_errors.ClientError(path="/settings", name="TypeError", message="x is undefined")
    assert client_errors.report(body).ok is True
    client_errors.report(body)
    lines = (tmp_path / "client-errors.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["path"] == "/settings" and first["message"] == "x is undefined"
