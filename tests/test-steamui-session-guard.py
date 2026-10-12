# SPDX-License-Identifier: MIT
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("guard", Path(__file__).parents[1] / "libexec/steamui-session-guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

profile_spec = importlib.util.spec_from_file_location("profile", Path(__file__).parents[1] / "libexec/steamui-headless-profile.py")
writer = importlib.util.module_from_spec(profile_spec)
profile_spec.loader.exec_module(writer)

class SessionGuard(unittest.TestCase):
    def test_publication_uses_validated_directory_descriptor(self):
        with tempfile.TemporaryDirectory(prefix="hguard-", dir="/tmp") as directory:
            parent = Path(directory) / "private"; parent.mkdir(mode=0o700)
            moved = Path(directory) / "moved"
            def replace_parent(fd):
                os.lseek(fd, 0, os.SEEK_SET)
                with os.fdopen(os.dup(fd)) as source:
                    self.assertEqual(json.load(source), {"test": "original"})
                parent.rename(moved); parent.mkdir(mode=0o700)
            with patch.object(writer.guard, "UID", os.getuid()), patch.object(writer.guard, "headless_fd", side_effect=replace_parent):
                writer.publish(parent / "profile.json", {"test": "original"})
            self.assertFalse((parent / "profile.json").exists())
            self.assertEqual(json.loads((moved / "profile.json").read_text()), {"test": "original"})
            self.assertEqual((moved / "profile.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in moved.iterdir()], ["profile.json"])
            with patch.object(writer.guard, "UID", os.getuid()), patch.object(writer.guard, "headless_fd"):
                with self.assertRaises(FileExistsError): writer.publish(moved / "profile.json", {})
            self.assertEqual([p.name for p in moved.iterdir()], ["profile.json"])
            alias = Path(directory) / "alias"; alias.symlink_to(moved)
            with patch.object(writer.guard, "UID", os.getuid()):
                with self.assertRaises(OSError): writer.publish(alias / "other.json", {})

    def test_locked_physical_cannot_pass(self):
        good = dict(User="1000", Name="user", Active="yes", Remote="no", Type="wayland", Seat="seat0", LockedHint="no", IdleHint="yes")
        guard.validate(good)
        for key, value in [("User", "0"), ("Active", "no"), ("Remote", "yes"), ("Type", "tty"), ("Seat", ""), ("LockedHint", "yes"), ("LockedHint", "")]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                guard.validate(dict(good, **{key: value}))

    def test_headless_selection_never_queries_physical(self):
        with patch.dict(os.environ, {"STEAMUI_HEADLESS_ENV": "owned-profile"}), patch.object(guard, "check", return_value={}) as check, patch.object(guard, "inspect", side_effect=AssertionError("physical query")):
            self.assertEqual(guard.select(), "headless")
            check.assert_called_once()

    def test_bad_headless_never_falls_back(self):
        with patch.dict(os.environ, {"STEAMUI_HEADLESS_ENV": "bad-profile"}), patch.object(guard, "check", side_effect=RuntimeError("stale")), patch.object(guard, "inspect", side_effect=AssertionError("physical query")):
            with self.assertRaises(RuntimeError):
                guard.select()

    @unittest.skipUnless(hasattr(socket, "SO_PEERCRED"), "Linux live peer credentials required")
    def test_owned_process_socket_identity_and_refusals(self):
        with tempfile.TemporaryDirectory(prefix="hguard-", dir="/tmp") as directory:
            base = Path(directory); proc = base / "proc"; runtime = base / "run"; runtime.mkdir(mode=0o700)
            uid = os.getuid(); token = "a" * 32; sway_pid = os.getpid()
            (proc / "sys/kernel/random").mkdir(parents=True)
            (proc / "sys/kernel/random/boot_id").write_text("boot1\n")
            def process(pid, comm, parent, start, env, argv):
                p = proc / str(pid); p.mkdir()
                fields = ["S", str(parent)] + ["0"] * 17 + [str(start)]
                (p / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
                (p / "comm").write_text(comm)
                (p / "environ").write_bytes(b"\0".join(k.encode() + b"=" + v.encode() for k, v in env.items()))
                (p / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv))
                (p / "fd").mkdir()
            process(sway_pid, "sway", 1, 100, {"WLR_BACKENDS": "headless", "AURORA_HEADLESS_TOKEN": token}, ["sway"])
            process(43, "Xwayland", sway_pid, 200, {}, ["Xwayland", ":5"])
            ipc = runtime / f"sway-ipc.{uid}.{sway_pid}.sock"; wayland = runtime / "wayland-5"
            sockets = []
            try:
                for path in [ipc, wayland]:
                    s = socket.socket(socket.AF_UNIX); s.bind(str(path)); s.listen(64); sockets.append(s)
                (proc / "net").mkdir()
                (proc / "net/unix").write_text("header\n000: 2 0 0 1 1 123 " + str(ipc) + "\n000: 2 0 0 1 1 124 " + str(wayland) + "\n")
                (proc / str(sway_pid) / "fd/1").symlink_to("socket:[123]")
                (proc / str(sway_pid) / "fd/2").symlink_to("socket:[124]")
                profile = base / "env.json"
                original = dict(boot_id="boot1", ownership_token=token, sway_pid=sway_pid, sway_start=100, xwayland_pid=43, xwayland_start=200, runtime_dir=str(runtime), SWAYSOCK=str(ipc), WAYLAND_DISPLAY="wayland-5", DISPLAY=":5")
                def write(j):
                    profile.write_text(json.dumps(j)); profile.chmod(0o600)
                write(original); self.assertEqual(guard.headless(profile, proc, uid)["DISPLAY"], ":5")
                for key, value in [("boot_id", "old"), ("sway_start", 101), ("xwayland_start", 201), ("ownership_token", "b" * 32), ("DISPLAY", ":0"), ("WAYLAND_DISPLAY", "wayland-0")]:
                    write(dict(original, **{key: value}))
                    with self.subTest(key=key), self.assertRaises((RuntimeError, OSError)):
                        guard.headless(profile, proc, uid)
                write(original); profile.chmod(0o644)
                with self.assertRaises(RuntimeError): guard.headless(profile, proc, uid)
                profile.chmod(0o600)
                fifo = base / "fifo"; os.mkfifo(fifo, 0o600)
                with self.assertRaises(RuntimeError): guard.headless(fifo, proc, uid)
                alias = base / "symlink.json"; alias.symlink_to(profile)
                with self.assertRaises(OSError): guard.headless(alias, proc, uid)
                # The validated descriptor remains bound to the original file.
                fd = os.open(profile, os.O_RDONLY)
                try:
                    profile.rename(base / "original.json")
                    write(dict(original, boot_id="stale"))
                    self.assertEqual(guard.headless_fd(fd, proc, uid)["DISPLAY"], ":5")
                    with self.assertRaises(RuntimeError): guard.headless(profile, proc, uid)
                finally: os.close(fd)
                write(original)
                (proc / str(sway_pid) / "fd/2").unlink()
                with self.assertRaises(RuntimeError): guard.headless(profile, proc, uid)
                (proc / str(sway_pid) / "fd/2").symlink_to("socket:[124]")
                # Old bound FD and proc-net path survive unlink; reachable peer changes.
                ipc.unlink()
                code = "import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen(4); print('ready',flush=True); sys.stdin.read()"
                replacement = subprocess.Popen([sys.executable, "-c", code, str(ipc)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(replacement.stdout.readline().strip(), "ready")
                    with self.assertRaisesRegex(RuntimeError, "reachable socket peer"):
                        guard.headless(profile, proc, uid)
                finally:
                    replacement.communicate("", timeout=5)
            finally:
                for s in sockets: s.close()

if __name__ == "__main__":
    unittest.main()
