"""Проверяет шаблон Compose с тестовыми значениями, без чтения локального .env."""

import json
import os
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("APP_", "HAPPYDAY_"))
    }
    result = subprocess.check_output(
        [
            "docker",
            "compose",
            "--env-file",
            "deploy/synology.env.example",
            "-f",
            "compose.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=root,
        env=env,
        text=True,
        encoding="utf-8",
    )
    config = json.loads(result)
    app = config["services"]["app"]
    assert app["environment"]["APP_ENV"] == "production"
    assert app["environment"]["APP_TELEGRAM_TOKEN"] == ""
    assert app["ports"][0]["host_ip"] == "127.0.0.1"
    assert str(app["ports"][0]["published"]) == "8787"
    assert app["ports"][0]["target"] == 8000
    assert app["read_only"] and app["init"]
    assert app["user"] == "10001:10001"
    assert app["volumes"][0]["target"] == "/data"
    assert app["volumes"][0]["bind"].get("create_host_path", False) is False
    assert app["restart"] == "unless-stopped"
    assert app["logging"]["options"]["max-file"] == "3"
    print("Compose interpolation, persistent data and loopback binding: OK")


if __name__ == "__main__":
    main()
