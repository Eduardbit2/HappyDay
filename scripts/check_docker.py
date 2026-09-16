"""Изолированная проверка образа без рабочей базы и Telegram."""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4

CAPS = ("CHOWN", "FOWNER", "DAC_OVERRIDE", "SETUID", "SETGID", "KILL")
CAP_ARGS = tuple(item for cap in CAPS for item in ("--cap-add", cap))


def docker(*args):
    if args[0] == "exec":
        args = ("exec", "--user", "10001:10001", *args[1:])
    return subprocess.check_output(["docker", *args], text=True, encoding="utf-8").strip()


def published_url(name):
    ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", name))
    return "http://127.0.0.1:" + ports["8000/tcp"][0]["HostPort"]


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
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    raise RuntimeError("Test container did not become healthy")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="happyday:ci")
    args = parser.parse_args()
    name = "happyday-smoke-" + uuid4().hex
    started = False
    volume_created = False
    try:
        docker("volume", "create", name + "-data")
        volume_created = True
        # Имитируем загруженную через File Station базу с чужим владельцем.
        docker(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,source={name}-data,target=/data",
            "--env",
            "APP_ENV=test",
            "--env",
            "APP_TELEGRAM_TOKEN=",
            args.image,
            "-c",
            "import os, sqlite3; from pathlib import Path; "
            "from app.db.migrations import upgrade_database; "
            "upgrade_database(Path('/data/happyday.db')); "
            "db=sqlite3.connect('/data/happyday.db'); "
            "db.execute(\"INSERT INTO groups(name) VALUES (?)\", ('Перенос Ё 🎂',)); "
            "db.commit(); db.close(); "
            "os.chown('/data/happyday.db', 12345, 12345); "
            "os.chmod('/data/happyday.db', 0o600); "
            "os.chown('/data', 12345, 12345); os.chmod('/data', 0o700)",
        )
        # Сам entrypoint должен выполнить произвольную команду уже без root.
        probe = docker(
            "run",
            "--rm",
            "--read-only",
            "--cap-drop",
            "ALL",
            *CAP_ARGS,
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=volume,source={name}-data,target=/data",
            args.image,
            "python",
            "-c",
            "import os, sqlite3; from pathlib import Path; "
            "assert os.getresuid() == (10001,)*3; "
            "assert os.getresgid() == (10001,)*3; assert os.getgroups() == []; "
            "status=dict(line.split(':', 1) for line in "
            "Path('/proc/self/status').read_text().splitlines()); "
            "assert all(int(status[k].strip(),16)==0 for k in "
            "('CapEff','CapPrm','CapInh','CapAmb')); "
            "assert int(status['NoNewPrivs']) == 1; "
            "assert Path('/data').stat().st_mode & 0o777 == 0o750; "
            "assert Path('/data/happyday.db').stat().st_mode & 0o777 == 0o640; "
            "db=sqlite3.connect('/data/happyday.db'); "
            'assert db.execute("SELECT name FROM groups WHERE name=?", '
            "('Перенос Ё 🎂',)).fetchone() == ('Перенос Ё 🎂',); "
            "assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',); "
            "db.close(); print('Entrypoint permissions and imported UTF-8 database: OK')",
        )
        print(probe)
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            *CAP_ARGS,
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=32m,mode=1777",
            "--mount",
            f"type=volume,source={name}-data,target=/data",
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
        base_url = published_url(name)
        wait_ready(name, base_url)
        # Проверяем UID реального Uvicorn, а не пользователя docker exec.
        docker(
            "exec",
            name,
            "python",
            "-c",
            "from pathlib import Path; "
            "processes=[p for p in Path('/proc').iterdir() if p.name.isdigit() "
            "and (p/'cmdline').read_bytes().split(b'\\x00')[1:3] == [b'-m', b'uvicorn']]; "
            "assert len(processes)==1; "
            "status=dict(line.split(':',1) for line in "
            "(processes[0]/'status').read_text().splitlines()); "
            "assert status['Uid'].split()==['10001']*4; "
            "assert status['Gid'].split()==['10001']*4; "
            "assert int(status['CapEff'].strip(),16)==0",
        )
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
        docker("exec", name, "python", "-m", "app.db", "backup")
        docker(
            "exec",
            name,
            "python",
            "-c",
            "from pathlib import Path; import shutil, sqlite3; "
            "backup=max(Path('/data/backups').glob('*.db')); "
            "Path('/data/restore').mkdir(); "
            "shutil.copyfile(backup, '/data/restore/happyday.db'); "
            "db=sqlite3.connect('/data/restore/happyday.db'); "
            "assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',); "
            "assert db.execute(\"SELECT name FROM groups WHERE name=?\", ('Ёжики 🎂',))."
            "fetchone() == ('Ёжики 🎂',); db.close()",
        )
        docker(
            "exec", "--env", "APP_DATA_DIR=/data/restore", name, "python", "-m", "app.db", "check"
        )
        docker("restart", "--time", "10", name)
        # Docker may assign a new ephemeral host port after restarting.
        base_url = published_url(name)
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
        print("Docker health, read-only root, UTF-8, backup/restore and restart: OK")
    except Exception:
        if started:
            print(docker("logs", "--tail", "60", name))
        raise
    finally:
        if started:
            docker("rm", "--force", name)
        if volume_created:
            docker("volume", "rm", name + "-data")


if __name__ == "__main__":
    main()
