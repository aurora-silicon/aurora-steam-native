#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write a nonclobbering owned-headless profile from the compositor's exec."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

spec = importlib.util.spec_from_file_location("guard", Path(__file__).with_name("steamui-session-guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

def create(path):
    if os.getuid() != guard.UID:
        raise RuntimeError("profile must be created by the legitimate test user")
    path = Path(path)
    if path.parent.is_symlink() or path.parent.stat().st_uid != guard.UID or path.parent.stat().st_mode & 0o077:
        raise RuntimeError("headless profile directory must be private and user-owned")
    token = os.environ.get("AURORA_HEADLESS_TOKEN", "")
    pid = os.getppid()
    for _ in range(16):
        p = guard.process(pid)
        if p["comm"] == "sway":
            break
        pid = p["parent"]
    else:
        raise RuntimeError("no owned compositor ancestor")
    children = []
    for child in Path("/proc").glob("[0-9]*"):
        try:
            c = guard.process(child.name)
            if c["uid"] == guard.UID and c["comm"] == "Xwayland" and c["parent"] == pid and os.environ.get("DISPLAY", "").encode() in c["argv"]:
                children.append((int(child.name), c))
        except (OSError, ValueError, IndexError):
            continue
    if len(children) != 1:
        raise RuntimeError("headless Xwayland absent or ambiguous; no physical fallback")
    child_pid, child = children[0]
    j = {k: os.environ.get(k, "") for k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "SWAYSOCK")}
    j.update(ownership_token=token, runtime_dir=os.environ.get("XDG_RUNTIME_DIR", ""), boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
             sway_pid=pid, sway_start=p["start"], xwayland_pid=child_pid, xwayland_start=child["start"],
             XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="sway")
    fd, temporary = tempfile.mkstemp(prefix=".headless-profile-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(j, out); out.flush(); os.fsync(out.fileno())
        guard.headless(temporary)
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: steamui-headless-profile.py PRIVATE_PROFILE")
    try:
        create(sys.argv[1])
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as error:
        raise SystemExit(str(error))
