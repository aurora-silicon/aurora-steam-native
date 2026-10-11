#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright 2026 Aurora Silicon
import argparse
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

MESA_VARS = frozenset(("LD_LIBRARY_PATH", "LIBGL_DRIVERS_PATH", "VK_DRIVER_FILES", "VK_ICD_FILENAMES",
                       "GBM_BACKENDS_PATH", "__EGL_VENDOR_LIBRARY_FILENAMES", "DRIRC_CONFIGDIR"))
ASSIGN = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
HEX = re.compile(r"^[0-9a-f]{64}$")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_wrapper(path):
    p = Path(path).absolute()
    if p.is_symlink():
        raise ValueError("wrapper must not be a symlink")
    return p.parent.resolve(strict=True) / p.name


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise ValueError("expected a regular file with one hard link")
    if st.st_size > 1048576:
        os.close(fd)
        raise ValueError("wrapper exceeds one MiB")
    return fd, st


def read_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    return os.read(fd, 1048577)


def prefixes(values):
    out = []
    for value in values:
        p = Path(value)
        if not p.is_absolute() or ".." in p.parts or str(p) in ("/", "/opt", "/usr", "/usr/lib", "/usr/lib64", "/home", "/var", "/tmp"):
            raise ValueError("retire-prefix must name a specific absolute installation prefix")
        if any(c in value for c in ':$`\\\n\r\0'):
            raise ValueError("retire-prefix must be a literal path without shell or list syntax")
        if any(c in value for c in "*?[]"):
            raise ValueError("retire-prefix does not accept wildcards")
        out.append(str(p).rstrip("/"))
    if not out:
        raise ValueError("at least one retire-prefix is required")
    return out


def split_paths(value, name):
    if any(c in value for c in '`\\;\n\r') or "$ (" in value or "$(" in value:
        raise ValueError("complex shell syntax requires a separately reviewed edit")
    atoms, start, i = [], 0, 0
    while i < len(value):
        if value[i:i + 2] == "${":
            end = value.find("}", i + 2)
            if end < 0 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?::-|-)?", value[i + 2:end]):
                raise ValueError("only simple variable references and empty defaults are supported")
            i = end + 1
            continue
        if value[i] == ":":
            atoms.append(value[start:i]); start = i + 1
        i += 1
    atoms.append(value[start:])
    return atoms


def transform(data, retired):
    text = data.decode("utf-8")
    if "\0" in text:
        raise ValueError("wrapper contains NUL")
    output, changes = [], []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n"); ending = line[len(body):]
        if not any(p in body for p in retired):
            output.append(line); continue
        m = ASSIGN.fullmatch(body)
        if not m or m.group(2) not in MESA_VARS:
            raise ValueError(f"line {number}: prefix reference outside a supported Mesa assignment")
        pre, name, value = m.groups()
        quote = value[:1] if value[:1] in ("'", '"') else ""
        if quote:
            if len(value) < 2 or not value.endswith(quote) or quote in value[1:-1]:
                raise ValueError(f"line {number}: unsupported quoting")
            value = value[1:-1]
        elif any(c.isspace() or c in "'\"" for c in value):
            raise ValueError(f"line {number}: non-literal assignment")
        atoms = split_paths(value, name)
        kept = [a for a in atoms if not any(a == p or a.startswith(p + "/") for p in retired)]
        if len(kept) == len(atoms):
            raise ValueError(f"line {number}: prefix embedded in an unsupported path expression")
        joined = ":".join(kept)
        self_reference = quote != chr(39) and joined in ("$" + name, "${" + name + "}", "${" + name + ":-}", "${" + name + "-}")
        replacement = "" if not joined or self_reference else f"{pre}{name}={quote}{joined}{quote}{ending}"
        output.append(replacement)
        changes.append(dict(line=number, variable=name, before=body, after=replacement.rstrip("\r\n")))
    result = "".join(output).encode()
    if any(p.encode() in result for p in retired):
        raise ValueError("retired prefix remains; refusing an incomplete edit")
    subprocess.run(["bash", "-n"], input=result, check=True, capture_output=True)
    return result, changes


def active_tool_processes(tool):
    found = []
    for p in Path("/proc").glob("[0-9]*"):
        if int(p.name) == os.getpid():
            continue
        try:
            paths = [Path(os.readlink(p / k)) for k in ("exe", "cwd")]
            comm = (p / "comm").read_text().strip()
            argv = (p / "cmdline").read_bytes().split(b"\0")
            selected = argv if comm.startswith(("wine", "FEX", "proton", "reaper")) else argv[:2]
            paths += [Path(a.decode(errors="replace")) for a in selected if a.startswith(b"/")]
            paths = [x.resolve(strict=False) for x in paths]
            if any(x == tool or tool in x.parents for x in paths):
                found.append(int(p.name))
        except (OSError, ValueError):
            pass
    return sorted(found)


def create_file(path, data, st=None):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data); f.flush()
        if st:
            os.fchmod(f.fileno(), stat.S_IMODE(st.st_mode))
            os.fchown(f.fileno(), st.st_uid, st.st_gid)
        os.fsync(f.fileno())


def replace_file(path, data, st):
    fd, temp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush()
            os.fchmod(f.fileno(), stat.S_IMODE(st.st_mode))
            os.fchown(f.fileno(), st.st_uid, st.st_gid)
            os.fsync(f.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main():
    ap = argparse.ArgumentParser(description="SHA-locked removal of explicit Mesa prefix pins from one Proton wrapper")
    ap.add_argument("mode", choices=("inspect", "audit", "plan", "apply", "rollback"))
    ap.add_argument("--wrapper", required=True)
    ap.add_argument("--expected-sha256")
    ap.add_argument("--source-lock", default=str(Path(__file__).resolve().parent.parent / "proton/guarded-proton-source-lock.json"))
    ap.add_argument("--retire-prefix", action="append", default=[])
    ap.add_argument("--record", help="new transaction JSON for apply; existing transaction JSON for rollback")
    args = ap.parse_args()
    wrapper = canonical_wrapper(args.wrapper)
    fd, st = open_regular(wrapper)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = read_fd(fd); before = digest(data)
        if args.mode not in ("inspect", "audit") and (not args.expected_sha256 or not HEX.fullmatch(args.expected_sha256) or before != args.expected_sha256):
            raise ValueError("current wrapper does not match required expected-sha256")
        running = active_tool_processes(wrapper.parent)
        if args.mode == "audit":
            lock = json.loads(Path(args.source_lock).read_text())
            rows = []
            for relative, expected in lock["historical_artifact_sha256"].items():
                part = Path(relative)
                if part.is_absolute() or ".." in part.parts or not HEX.fullmatch(expected):
                    raise ValueError("invalid source-lock artifact entry")
                path = (wrapper.parent / part).resolve(strict=True)
                if wrapper.parent not in path.parents: raise ValueError("artifact escapes the tool")
                with path.open("rb") as artifact: actual = hashlib.file_digest(artifact, "sha256").hexdigest()
                rows.append(dict(path=relative, expected=expected, sha256=actual, matches=actual == expected))
            if not rows: raise ValueError("empty source-lock artifact ledger")
            ok = all(row["matches"] for row in rows)
            print(json.dumps(dict(wrapper_sha256=before, recorded_artifact_correspondence=ok, artifacts=rows), indent=2))
            if not ok: raise ValueError("tool differs from its recorded public-source build")
            return
        if args.mode == "inspect":
            print(json.dumps(dict(wrapper=str(wrapper), sha256=before, uid=st.st_uid, gid=st.st_gid,
                                  mode=oct(stat.S_IMODE(st.st_mode)), active_tool_pids=running), indent=2)); return
        if args.mode == "rollback":
            if not args.record: raise ValueError("rollback needs its transaction record")
            record = json.loads(Path(args.record).read_text())
            if record["wrapper"] != str(wrapper) or record["after_sha256"] != before:
                raise ValueError("record does not correspond to the current wrapper")
            backup = Path(record["backup"])
            if backup.parent != wrapper.parent or backup.name != wrapper.name + ".mesa-routing." + record["before_sha256"][:16] + ".bak":
                raise ValueError("unexpected backup path")
            bfd, _ = open_regular(backup)
            try: replacement = read_fd(bfd)
            finally: os.close(bfd)
            if digest(replacement) != record["before_sha256"]:
                raise ValueError("backup hash mismatch")
            subprocess.run(["bash", "-n"], input=replacement, check=True, capture_output=True)
            changes = []
        else:
            retired = prefixes(args.retire_prefix)
            replacement, changes = transform(data, retired)
            if args.mode == "apply" and replacement == data:
                print(json.dumps(dict(mode="apply", changed=False, sha256=before))); return
        report = dict(wrapper=str(wrapper), before_sha256=before, after_sha256=digest(replacement), changes=changes,
                      active_tool_pids=running, diff="".join(difflib.unified_diff(data.decode().splitlines(True), replacement.decode().splitlines(True), n=0)))
        if args.mode == "plan": print(json.dumps(report, indent=2)); return
        if running: raise ValueError(f"tool is in use by PIDs {running}")
        if not args.record: raise ValueError("apply needs a new transaction record path")
        if args.mode == "apply":
            record_path = Path(args.record).absolute()
            if record_path.exists() or record_path.is_symlink(): raise ValueError("transaction record already exists")
            backup = wrapper.with_name(wrapper.name + ".mesa-routing." + before[:16] + ".bak")
            if backup.exists() or backup.is_symlink():
                bfd, _ = open_regular(backup)
                try: backup_data = read_fd(bfd)
                finally: os.close(bfd)
                if digest(backup_data) != before: raise ValueError("existing backup hash mismatch")
            else: create_file(backup, data, st)
            report.update(backup=str(backup), retired_prefixes=retired, uid=st.st_uid, gid=st.st_gid, mode=stat.S_IMODE(st.st_mode))
            create_file(record_path, (json.dumps(report, indent=2) + "\n").encode())
        now = wrapper.stat(follow_symlinks=False)
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino) or digest(read_fd(fd)) != before:
            raise ValueError("wrapper changed during planning")
        if active_tool_processes(wrapper.parent): raise ValueError("tool became active during planning")
        replace_file(wrapper, replacement, st)
        print(json.dumps(dict(mode=args.mode, wrapper=str(wrapper), sha256=digest(replacement), backup=str(backup)), indent=2))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as e:
        print(f"REFUSE: {e}", file=sys.stderr); sys.exit(2)
