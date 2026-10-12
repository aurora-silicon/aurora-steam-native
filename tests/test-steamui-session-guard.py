# SPDX-License-Identifier: MIT
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("guard", Path(__file__).parents[1] / "libexec/steamui-session-guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

class SessionGuard(unittest.TestCase):
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

    def test_owned_process_socket_identity_and_refusals(self):
        with tempfile.TemporaryDirectory(prefix="hguard-", dir="/tmp") as directory:
            base = Path(directory); proc = base / "proc"; runtime = base / "run"; runtime.mkdir(mode=0o700)
            uid = os.getuid(); token = "a" * 32
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
            process(42, "sway", 1, 100, {"WLR_BACKENDS": "headless", "AURORA_HEADLESS_TOKEN": token}, ["sway"])
            process(43, "Xwayland", 42, 200, {}, ["Xwayland", ":5"])
            ipc = runtime / f"sway-ipc.{uid}.42.sock"; wayland = runtime / "wayland-5"
            sockets = []
            try:
                for path in [ipc, wayland]:
                    s = socket.socket(socket.AF_UNIX); s.bind(str(path)); sockets.append(s)
                (proc / "net").mkdir()
                (proc / "net/unix").write_text("header\n000: 2 0 0 1 1 123 " + str(ipc) + "\n000: 2 0 0 1 1 124 " + str(wayland) + "\n")
                (proc / "42/fd/1").symlink_to("socket:[123]")
                (proc / "42/fd/2").symlink_to("socket:[124]")
                profile = base / "env.json"
                original = dict(boot_id="boot1", ownership_token=token, sway_pid=42, sway_start=100, xwayland_pid=43, xwayland_start=200, runtime_dir=str(runtime), SWAYSOCK=str(ipc), WAYLAND_DISPLAY="wayland-5", DISPLAY=":5")
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
                (proc / "42/fd/2").unlink()
                with self.assertRaises(RuntimeError): guard.headless(profile, proc, uid)
            finally:
                for s in sockets: s.close()

if __name__ == "__main__":
    unittest.main()
