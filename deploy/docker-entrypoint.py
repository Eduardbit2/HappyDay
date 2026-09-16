"""Подготовить bind mount и заменить процесс приложением без прав root."""

import os
import stat
import sys
from pathlib import Path

UID = GID = 10001


def prepare_data():
    # Только /data и сама база: не следуем ссылкам и не обходим чужие подкаталоги.
    directory = os.open("/data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            database = os.open(
                "happyday.db", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
        except FileNotFoundError:
            database = None
        try:
            if database is not None and not stat.S_ISREG(os.fstat(database).st_mode):
                raise RuntimeError("/data/happyday.db must be a regular file")
            os.fchown(directory, UID, GID)
            os.fchmod(directory, 0o750)
            if database is not None:
                os.fchown(database, UID, GID)
                os.fchmod(database, 0o640)
        finally:
            if database is not None:
                os.close(database)
    finally:
        os.close(directory)


def main():
    if len(sys.argv) < 2:
        raise RuntimeError("No application command supplied")
    if os.geteuid() == 0:
        prepare_data()
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
    if os.getresuid() != (UID, UID, UID) or os.getresgid() != (GID, GID, GID):
        raise RuntimeError("HappyDay must run as 10001:10001")
    if any(group != GID for group in os.getgroups()):
        raise RuntimeError("Unexpected supplementary groups")
    # Linux очищает capabilities при переходе root -> UID 10001. Проверяем результат.
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    if any(int(status[key].strip(), 16) for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")):
        raise RuntimeError("Application capabilities were not cleared")
    os.environ["HOME"] = "/home/happyday"
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"HappyDay startup failed: {error}", file=sys.stderr)
        sys.exit(1)
