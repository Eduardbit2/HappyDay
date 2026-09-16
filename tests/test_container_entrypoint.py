"""Отказ запуска при ошибках подготовки/сброса прав; Linux проверяет Docker smoke."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def entrypoint(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "deploy/docker-entrypoint.py"
    spec = importlib.util.spec_from_file_location("container_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.os = SimpleNamespace(
        geteuid=Mock(return_value=0),
        setgroups=Mock(),
        setgid=Mock(),
        setuid=Mock(),
        getresuid=Mock(return_value=(10001,) * 3),
        getresgid=Mock(return_value=(10001,) * 3),
        getgroups=Mock(return_value=[]),
        environ={},
        execvp=Mock(),
    )
    module.prepare_data = Mock()
    module.Path = Mock()
    module.Path.return_value.read_text.return_value = "CapEff: 0\nCapPrm: 0\nCapInh: 0\nCapAmb: 0\n"
    monkeypatch.setattr(module.sys, "argv", [str(path), "python", "-m", "app.db", "check"])
    return module


@pytest.mark.parametrize("step", ["prepare_data", "setgroups", "setgid", "setuid"])
def test_startup_failure_never_launches_command(entrypoint, step):
    target = entrypoint if step == "prepare_data" else entrypoint.os
    getattr(target, step).side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        entrypoint.main()
    entrypoint.os.execvp.assert_not_called()


@pytest.mark.parametrize("field", ["getresuid", "getresgid", "getgroups"])
def test_remaining_root_identity_prevents_launch(entrypoint, field):
    getattr(entrypoint.os, field).return_value = (0,)
    with pytest.raises(RuntimeError):
        entrypoint.main()
    entrypoint.os.execvp.assert_not_called()


@pytest.mark.parametrize("capability", ["CapEff", "CapPrm", "CapInh", "CapAmb"])
def test_remaining_capability_prevents_launch(entrypoint, capability):
    entrypoint.Path.return_value.read_text.return_value = "\n".join(
        f"{key}: {1 if key == capability else 0}"
        for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")
    )
    with pytest.raises(RuntimeError, match="capabilities"):
        entrypoint.main()
    entrypoint.os.execvp.assert_not_called()


def test_command_arguments_preserved_after_preparation(entrypoint):
    entrypoint.main()
    entrypoint.prepare_data.assert_called_once()
    entrypoint.os.execvp.assert_called_once_with("python", ["python", "-m", "app.db", "check"])


def test_explicit_app_user_skips_root_preparation(entrypoint):
    entrypoint.os.geteuid.return_value = 10001
    entrypoint.main()
    entrypoint.prepare_data.assert_not_called()
    entrypoint.os.setuid.assert_not_called()
    entrypoint.os.execvp.assert_called_once()
