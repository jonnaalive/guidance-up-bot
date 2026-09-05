import base64
import gzip
import json
import sys
from unittest.mock import Mock

import pytest

from scripts import runtime_state


def response(payload):
    return Mock(ok=True, status_code=200, json=lambda: payload, raise_for_status=Mock())


def test_durable_delivery_roundtrip_and_conflict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    original = {"since": "2026-09-05T00:00:00Z", "sent": {"existing": "yesterday"}}
    archive = {"delivery.json": base64.b64encode(json.dumps(original).encode()).decode()}
    content = base64.b64encode(gzip.compress(json.dumps(archive).encode())).decode()
    remote = {"sha": "original", "encoding": "base64", "content": content}
    session = Mock()
    session.get.return_value = response(remote)
    session.put.return_value = response({})
    monkeypatch.setattr(runtime_state.requests, "Session", lambda: session)
    monkeypatch.setattr(sys, "argv", ["runtime_state", "restore", "digest-state", "runtime"])
    runtime_state.main()
    assert json.loads((tmp_path / "runtime/delivery.json").read_text()) == original
    monkeypatch.setattr(sys, "argv", ["runtime_state", "save", "digest-state", "runtime"])
    runtime_state.main()
    saved = session.put.call_args.kwargs["json"]
    assert saved["sha"] == "original"
    decoded = json.loads(gzip.decompress(base64.b64decode(saved["content"])))
    assert json.loads(base64.b64decode(decoded["delivery.json"])) == original
    remote["sha"] = "concurrent-update"
    with pytest.raises(RuntimeError, match="Concurrent"):
        runtime_state.main()
    assert session.put.call_count == 1


def test_restore_api_failure_is_not_empty_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    session = Mock()
    session.get.return_value = Mock(status_code=500, raise_for_status=Mock(side_effect=RuntimeError("API unavailable")))
    monkeypatch.setattr(runtime_state.requests, "Session", lambda: session)
    monkeypatch.setattr(sys, "argv", ["runtime_state", "restore", "digest-state", "runtime"])
    with pytest.raises(RuntimeError, match="API unavailable"):
        runtime_state.main()
    assert not (tmp_path / "runtime").exists()
