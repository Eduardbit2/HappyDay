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


def test_smoke_refreshes_port_after_restart(monkeypatch):
    ports = iter(("12345", "23456"))
    checked = []
    commands = []

    def fake_docker(*args):
        commands.append(args)
        if args[0] == "inspect":
            return json.dumps({"8000/tcp": [{"HostPort": next(ports)}]})
        if args[0] == "exec" and args[-2:] == ("id", "-u"):
            return "10001"
        return ""

    monkeypatch.setattr(check_docker, "docker", fake_docker)
    monkeypatch.setattr(check_docker, "wait_ready", lambda name, url: checked.append(url))
    monkeypatch.setattr(
        check_docker.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO("Логин".encode()),
    )
    monkeypatch.setattr("sys.argv", ["check_docker.py"])
    check_docker.main()
    assert checked == ["http://127.0.0.1:12345", "http://127.0.0.1:23456"]
    assert commands[-2][0:2] == ("rm", "--force")
    assert commands[-1][0:2] == ("volume", "rm")
