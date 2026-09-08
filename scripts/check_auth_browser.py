"""Изолированная браузерная проверка: python scripts/check_auth_browser.py."""

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description="Проверка авторизации HappyDay в браузере")
    parser.add_argument("--channel", default="msedge" if os.name == "nt" else "")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "test-results" / ("browser-" + uuid4().hex)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("APP_")},
        "APP_ENV": "test",
        "APP_TELEGRAM_TOKEN": "",
        "APP_DATA_DIR": str(data_dir),
        "APP_BASE_URL": base_url,
        "APP_ALLOWED_HOSTS": '["127.0.0.1","localhost"]',
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(root),
        "HAPPYDAY_BROWSER_URL": base_url,
        "HAPPYDAY_BROWSER_CHANNEL": args.channel,
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
            "--no-access-log",
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        for _ in range(150):
            if server.poll() is not None:
                raise RuntimeError("Тестовый сервер завершился раньше времени")
            try:
                with urllib.request.urlopen(base_url + "/health/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Тестовый сервер не запустился")
        result = subprocess.run(
            [sys.executable, str(root / "tests/browser_auth.py")], cwd=root, env=env
        )
        print(f"Артефакты: {data_dir}")
        return result.returncode
    finally:
        server.terminate()
        try:
            server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()


if __name__ == "__main__":
    raise SystemExit(main())
