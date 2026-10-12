#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Select a verified owned headless display or an unlocked physical session."""
import argparse
import json
import os
import re
import stat
import socket
import struct
import subprocess
from pathlib import Path

UID = 1000
KEYS = ("User", "Name", "Active", "Remote", "Type", "Seat", "LockedHint")

def process(pid, proc=Path("/proc")):
    p = proc / str(int(pid))
    fields = (p / "stat").read_text().rsplit(")", 1)[1].split()
    return {"uid": p.stat().st_uid, "comm": (p / "comm").read_text().strip(),
            "parent": int(fields[1]), "start": int(fields[19]),
            "argv": (p / "cmdline").read_bytes().split(b"\0"),
            "cgroups": (p / "cgroup").read_text().splitlines(),
            "env": dict(v.split(b"=", 1) for v in (p / "environ").read_bytes().split(b"\0") if b"=" in v)}

def unit_group(process, unit):
    if not re.fullmatch(r"aurora-steamui-headless-[0-9a-f]{16}\.service", unit):
        raise RuntimeError("dedicated headless unit missing")
    groups = [line[3:] for line in process["cgroups"] if line.startswith("0::")]
    if len(groups) != 1 or Path(groups[0]).name != unit:
        raise RuntimeError("process is not in the dedicated headless unit")
    return groups[0]

def socket_inodes(pid, proc):
    inodes = set()
    for fd in (proc / str(pid) / "fd").iterdir():
        match = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(fd))
        if match:
            inodes.add(match[1])
    return inodes

def xwayland_listeners(xwayland, xpid, sway_pid, display, proc):
    argv = xwayland["argv"]
    listeners = []
    for index, arg in enumerate(argv):
        if arg in (b"-listenfd", b"-listen"):
            if index + 1 >= len(argv) or not argv[index + 1].isdigit():
                raise RuntimeError("invalid Xwayland inherited listener")
            link = os.readlink(proc / str(xpid) / "fd" / argv[index + 1].decode())
            match = re.fullmatch(r"socket:\[(\d+)\]", link)
            if not match:
                raise RuntimeError("Xwayland listener is not a socket")
            listeners.append(match[1])
    if len(listeners) != 2 or len(set(listeners)) != 2 or not set(listeners) <= socket_inodes(sway_pid, proc):
        raise RuntimeError("Xwayland listeners are not inherited from owned compositor")
    path = "/tmp/.X11-unix/X" + display[1:].split(".", 1)[0]
    bound = set()
    for line in (proc / "net/unix").read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) == 8 and fields[6] in listeners and fields[7] in (path, "@" + path):
            bound.add(fields[6])
    if bound != set(listeners):
        raise RuntimeError("Xwayland inherited listener display mismatch")

def owned_socket(path, pid, proc, uid):
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != uid:
        raise RuntimeError("owned headless socket absent")
    inodes = socket_inodes(pid, proc)
    bound = False
    for line in (proc / "net/unix").read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) == 8 and fields[6] in inodes and fields[7] == str(path):
            bound = True
    if not bound:
        raise RuntimeError("display socket is not bound by the owned compositor")
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("live socket peer verification unsupported")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(path))
        peer_pid, peer_uid, _ = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        if peer_pid != int(pid) or peer_uid != uid:
            raise RuntimeError("reachable socket peer is not the owned compositor")

def headless(profile, proc=Path("/proc"), uid=UID):
    fd = os.open(profile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        return headless_fd(fd, proc, uid)
    finally:
        os.close(fd)

def headless_fd(fd, proc=Path("/proc"), uid=UID):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
        raise RuntimeError("headless profile must be private and owned by the test user")
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), "r") as source:
        j = json.load(source)
    if j["boot_id"] != (proc / "sys/kernel/random/boot_id").read_text().strip():
        raise RuntimeError("stale headless boot identity")
    token = j.get("ownership_token", "")
    if len(token) < 32 or not all(c in "0123456789abcdef" for c in token):
        raise RuntimeError("headless ownership token missing")
    sway = process(j["sway_pid"], proc)
    if sway["uid"] != uid or sway["comm"] != "sway" or sway["start"] != j["sway_start"]:
        raise RuntimeError("headless compositor identity changed")
    if sway["env"].get(b"WLR_BACKENDS") != b"headless" or sway["env"].get(b"AURORA_HEADLESS_TOKEN") != token.encode():
        raise RuntimeError("compositor is not this owned headless test")
    group = unit_group(sway, j["owned_unit"])
    runtime = Path(j["runtime_dir"])
    if runtime.is_symlink() or runtime.stat().st_uid != uid or runtime.stat().st_mode & 0o077:
        raise RuntimeError("headless runtime directory ownership mismatch")
    socket = Path(j["SWAYSOCK"])
    if socket.parent != runtime or socket.name != f"sway-ipc.{uid}.{int(j['sway_pid'])}.sock":
        raise RuntimeError("headless IPC path mismatches owned compositor")
    if not re.fullmatch(r"wayland-[0-9]+", j.get("WAYLAND_DISPLAY", "")):
        raise RuntimeError("invalid headless Wayland display")
    owned_socket(socket, j["sway_pid"], proc, uid)
    owned_socket(runtime / j["WAYLAND_DISPLAY"], j["sway_pid"], proc, uid)
    xwayland = process(j["xwayland_pid"], proc)
    if (xwayland["uid"] != uid or xwayland["comm"] != "Xwayland" or
            xwayland["start"] != j["xwayland_start"] or
            unit_group(xwayland, j["owned_unit"]) != group or
            xwayland["env"].get(b"AURORA_HEADLESS_TOKEN") != token.encode() or
            len(xwayland["argv"]) < 2 or xwayland["argv"][1] != j["DISPLAY"].encode()):
        raise RuntimeError("DISPLAY is not the owned headless Xwayland")
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", j["DISPLAY"]):
        raise RuntimeError("headless display environment incomplete")
    xwayland_listeners(xwayland, j["xwayland_pid"], j["sway_pid"], j["DISPLAY"], proc)
    authority = j.get("XAUTHORITY", "")
    if authority:
        argv = xwayland["argv"]
        if b"-auth" not in argv or argv.index(b"-auth") + 1 >= len(argv) or argv[argv.index(b"-auth") + 1] != authority.encode():
            raise RuntimeError("headless authority does not belong to its Xwayland")
        st = Path(authority).lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != uid:
            raise RuntimeError("headless authority ownership mismatch")
    # Re-read both process identities after inspection; never fall back to GNOME.
    if process(j["sway_pid"], proc)["start"] != sway["start"] or process(j["xwayland_pid"], proc)["start"] != xwayland["start"]:
        raise RuntimeError("headless process identity raced")
    return j

def inspect(sid):
    args = ["loginctl", "show-session", sid] + [a for k in KEYS for a in ("-p", k)]
    p = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
    return dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)

def validate(session):
    if (session.get("User") != str(UID) or session.get("Name") != "user" or
            session.get("Active") != "yes" or session.get("Remote") != "no" or
            session.get("Type") not in ("wayland", "x11") or not session.get("Seat", "").startswith("seat") or
            session.get("LockedHint", "").lower() not in ("no", "false")):
        raise RuntimeError("physical session absent/ambiguous/locked; legitimate unlock required")

def check():
    profile = os.environ.get("STEAMUI_HEADLESS_ENV")
    if profile:
        return headless(profile)
    sid = os.environ.get("STEAMUI_SESSION_ID")
    if not sid:
        raise RuntimeError("physical session identity missing")
    validate(inspect(sid))
    return None

def select():
    if os.environ.get("STEAMUI_HEADLESS_ENV"):
        check()
        return "headless"
    sid = os.environ.get("STEAMUI_SESSION_ID")
    if sid:
        validate(inspect(sid))
        return sid
    p = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True, timeout=10, check=True)
    matches = []
    for line in p.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1:3] != [str(UID), "user"]:
            continue
        try:
            validate(inspect(fields[0]))
            matches.append(fields[0])
        except RuntimeError:
            pass
    if len(matches) != 1:
        raise RuntimeError("unlocked physical session missing or ambiguous")
    return matches[0]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select", action="store_true")
    args = parser.parse_args()
    try:
        if args.select:
            print(select())
        else:
            check()
    except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(2, str(error) + "\n")
