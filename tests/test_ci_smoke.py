import io
import json

import pytest

from scripts import check_docker


def test_readiness_retries_connection_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(
        check_docker,
        "docker",
        lambda *args: json.dumps({"Running": True, "Health": {"Status": "healthy"}}),
    )
    monkeypatch.setattr(check_docker.time, "sleep", lambda _: None)

    def open_url(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionResetError("Container is still starting")
        return io.BytesIO(b'{"status":"ready"}')

    monkeypatch.setattr(check_docker.urllib.request, "urlopen", open_url)
    check_docker.wait_ready("test-container", "http://127.0.0.1:12345")
    assert len(calls) == 2


def test_readiness_rejects_stopped_container(monkeypatch):
    monkeypatch.setattr(check_docker, "docker", lambda *args: '{"Running":false}')
    with pytest.raises(RuntimeError, match="stopped before readiness"):
        check_docker.wait_ready("test-container", "http://127.0.0.1:12345")
