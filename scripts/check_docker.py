"""Изолированная проверка образа без рабочей базы и Telegram."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, encoding="utf-8").strip()


def wait_ready(name, base_url):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        if not state["Running"]:
            raise RuntimeError("Test container stopped before readiness")
        try:
            with urllib.request.urlopen(base_url + "/health/ready", timeout=3) as response:
                ready = json.load(response) == {"status": "ready"}
            if ready and state.get("Health", {}).get("Status") == "healthy":
                return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError("Test container did not become healthy")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="happyday:ci")
    args = parser.parse_args()
    name = "happyday-smoke-" + uuid4().hex
    started = False
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            "127.0.0.1::8000",
            "--env",
            "APP_ENV=test",
            "--env",
            "APP_TELEGRAM_TOKEN=",
            "--env",
            "APP_SCHEDULER_ENABLED=false",
            args.image,
        )
        started = True
        ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name))
        base_url = "http://127.0.0.1:" + ports["8000/tcp"][0]["HostPort"]
        wait_ready(name, base_url)
        assert docker("exec", name, "id", "-u") == "10001"
        with urllib.request.urlopen(base_url + "/login", timeout=5) as response:
            assert "Логин" in response.read().decode("utf-8")
        docker(
            "exec",
            name,
            "python",
            "-c",
            "from pathlib import Path; import importlib.util; "
            "assert not Path('/app/.env').exists(); "
            "assert importlib.util.find_spec('pytest') is None",
        )
        docker(
            "exec",
            name,
            "python",
            "-c",
            "import sqlite3; "
            "db=sqlite3.connect('/data/happyday.db'); "
            "db.execute(\"INSERT INTO groups(name) VALUES (?)\", ('Ёжики 🎂',)); "
            "db.commit(); db.close()",
        )
        docker("restart", "--time", "10", name)
        wait_ready(name, base_url)
        docker(
            "exec",
            name,
            "python",
            "-c",
            "import sqlite3; db=sqlite3.connect('/data/happyday.db'); "
            "assert db.execute(\"SELECT name FROM groups WHERE name=?\", ('Ёжики 🎂',))."
            "fetchone() == ('Ёжики 🎂',); db.close()",
        )
        print("Docker startup, health, UTF-8, non-root and restart: OK")
    finally:
        if started:
            docker("rm", "--force", name)


if __name__ == "__main__":
    main()
