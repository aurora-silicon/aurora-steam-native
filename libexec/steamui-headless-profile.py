#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write a nonclobbering owned-headless profile from the compositor's exec."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import stat
import secrets

spec = importlib.util.spec_from_file_location("guard", Path(__file__).with_name("steamui-session-guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

def create(path):
    if os.getuid() != guard.UID:
        raise RuntimeError("profile must be created by the legitimate test user")
    path = Path(path)
    token = os.environ.get("AURORA_HEADLESS_TOKEN", "")
    pid = os.getppid()
    for _ in range(16):
        p = guard.process(pid)
        if p["comm"] == "sway":
            break
        pid = p["parent"]
    else:
        raise RuntimeError("no owned compositor ancestor")
    unit = os.environ.get("AURORA_HEADLESS_UNIT", "")
    group = guard.unit_group(p, unit)
    children = []
    for child in Path("/proc").glob("[0-9]*"):
        try:
            c = guard.process(child.name)
            if c["uid"] == guard.UID and c["comm"] == "Xwayland" and guard.unit_group(c, unit) == group and c["env"].get(b"AURORA_HEADLESS_TOKEN") == token.encode() and len(c["argv"]) > 1 and c["argv"][1] == os.environ.get("DISPLAY", "").encode():
                children.append((int(child.name), c))
        except (OSError, ValueError, IndexError, RuntimeError):
            continue
    if len(children) != 1:
        raise RuntimeError("headless Xwayland absent or ambiguous; no physical fallback")
    child_pid, child = children[0]
    j = {k: os.environ.get(k, "") for k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "SWAYSOCK")}
    j.update(ownership_token=token, owned_unit=unit, runtime_dir=os.environ.get("XDG_RUNTIME_DIR", ""), boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
             sway_pid=pid, sway_start=p["start"], xwayland_pid=child_pid, xwayland_start=child["start"],
             XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="sway")
    publish(path, j)

def publish(path, j):
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    st = os.fstat(directory)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != guard.UID or st.st_mode & 0o077:
        os.close(directory)
        raise RuntimeError("headless profile directory must be private and user-owned")
    temporary = ".headless-profile-" + secrets.token_hex(16)
    fd = None
    try:
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(os.dup(fd), "w") as out:
            json.dump(j, out); out.flush(); os.fsync(out.fileno())
        guard.headless_fd(fd)
        os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.fsync(directory)
    finally:
        try:
            if fd is not None:
                os.close(fd)
                os.unlink(temporary, dir_fd=directory)
        finally:
            os.close(directory)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: steamui-headless-profile.py PRIVATE_PROFILE")
    try:
        create(sys.argv[1])
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as error:
        raise SystemExit(str(error))
