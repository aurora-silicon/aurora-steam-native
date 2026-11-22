# SPDX-License-Identifier: MIT
"""The launch loop's process contract: open-file limit, FEX graphics provider, the exported
userpages launcher and the host libpng preload."""
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import unittest

LOOP = Path(__file__).parents[1] / "libexec/steam-native4k-launch-loop.sh"


class LaunchEnvironment(unittest.TestCase):
    def run_loop(self, provider_exists=True, caller_provider=None, soft_nofile=None, launcher_from_path=False,
                 libpng=None, caller_preload=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam/steamrtarm64/steam"
            steam.parent.mkdir(parents=True)
            report = root / "report"
            # Stands in for the client: record the preload it starts with.
            steam.write_text("#!/bin/sh\necho \"preload=${LD_PRELOAD-<unset>}\" >> \"$REPORT\"\n")
            steam.chmod(0o755)
            # Stands in for userpages: record what the loop exported, then run the restart
            # loop it was given (without the page-size argument), as userpages does.
            launcher = root / "userpages"
            launcher.write_text("#!/bin/bash\n{ echo \"nofile=$(ulimit -Sn) $(ulimit -Hn)\";"
                                " echo \"provider=${STEAM_COMPAT_GRAPHICS_PROVIDER-<unset>}\";"
                                " echo \"launcher=${AURORA_USERPAGES_LAUNCHER-<unset>}\";"
                                " echo \"launcher_preload=${LD_PRELOAD-<unset>}\"; } > \"$REPORT\"\n"
                                "shift\nexec \"$@\"\n")
            launcher.chmod(0o755)
            # The loop checks that the client is an AArch64 ELF with python3; the stand-in
            # client is a script, so a python3 on PATH records its preload and accepts it.
            tools = root / "tools"; tools.mkdir()
            (tools / "python3").write_text("#!/bin/sh\necho \"check_preload=${LD_PRELOAD-<unset>}\" >> \"$REPORT\"\n")
            (tools / "python3").chmod(0o755)
            # The stand-in userpages does not change the page size, so on a 16 KiB host the
            # loop's 4K check would fail: report 4096, as it reads under the real `userpages 4k`.
            getconf = shutil.which("getconf") or "/usr/bin/getconf"
            (tools / "getconf").write_text("#!/bin/sh\ncase \"$1\" in PAGE_SIZE|PAGESIZE) echo 4096;;"
                                           f" *) exec {getconf} \"$@\";; esac\n")
            (tools / "getconf").chmod(0o755)
            provider = root / "aurora-fex-mesa/graphics_provider.json"
            if provider_exists:
                provider.parent.mkdir()
                provider.write_text("{}\n")
            env = {"PATH": str(tools) + os.pathsep + os.environ["PATH"], "HOME": str(root), "REPORT": str(report),
                   "AURORA_STEAM_ROOT": str(root / "steam"), "AURORA_USERPAGES_LAUNCHER": str(launcher),
                   "AURORA_FEX_GRAPHICS_PROVIDER": str(provider),
                   "AURORA_STEAM_CONSOLE_LOG": str(root / "console.log")}
            # "fake" stands in for a host libpng; any other value is passed through as given.
            if libpng == "fake":
                libpng = str(root / "libpng16.so.16")
                Path(libpng).write_text("")
            if libpng is not None:
                env["AURORA_STEAM_LIBPNG"] = libpng
            if caller_preload is not None:
                env["LD_PRELOAD"] = caller_preload
            if caller_provider is not None:
                env["STEAM_COMPAT_GRAPHICS_PROVIDER"] = caller_provider
            if launcher_from_path:
                # No caller choice: the loop must find `userpages` on PATH and export it itself.
                bindir = root / "bin"; bindir.mkdir()
                (bindir / "userpages").symlink_to(launcher)
                del env["AURORA_USERPAGES_LAUNCHER"]
                env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
            def limit():
                if soft_nofile is not None:
                    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
                    resource.setrlimit(resource.RLIMIT_NOFILE, (soft_nofile, hard))
            subprocess.run([str(LOOP)], env=env, preexec_fn=limit, check=True, timeout=30)
            values = dict(line.split("=", 1) for line in report.read_text().splitlines())
            return values, str(provider), str(root / "bin/userpages"), libpng

    def test_installed_aurora_provider_is_exported(self):
        values, provider, _, _ = self.run_loop()
        self.assertEqual(values["provider"], provider)

    def test_caller_choice_of_provider_wins(self):
        values, _, _, _ = self.run_loop(caller_provider="/elsewhere/graphics_provider.json")
        self.assertEqual(values["provider"], "/elsewhere/graphics_provider.json")

    def test_absent_provider_leaves_steam_default(self):
        values, _, _, _ = self.run_loop(provider_exists=False)
        self.assertEqual(values["provider"], "<unset>")

    def test_soft_open_file_limit_is_raised_to_the_hard_limit(self):
        hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if hard != resource.RLIM_INFINITY and hard <= 256:
            self.skipTest("hard open-file limit too low to observe a raise")
        values, _, _, _ = self.run_loop(soft_nofile=256)
        soft, reported_hard = values["nofile"].split()
        self.assertEqual(soft, reported_hard)
        self.assertNotEqual(soft, "256")

    def test_userpages_launcher_is_exported_to_steam_children(self):
        # Proton tools launched by Steam read AURORA_USERPAGES_LAUNCHER from their environment.
        values, _, found, _ = self.run_loop(launcher_from_path=True)
        self.assertEqual(values["launcher"], found)

    def test_host_libpng_is_preloaded(self):
        # The client's exported private libpng must not mix with the host copy SDL3_image loads.
        values, _, _, libpng = self.run_loop(libpng="fake")
        self.assertEqual(values["preload"], libpng)

    def test_libpng_preload_is_given_to_the_client_alone(self):
        # The launcher and the checks before the client run without it.
        values, _, _, _ = self.run_loop(libpng="fake")
        self.assertEqual(values["launcher_preload"], "<unset>")
        self.assertEqual(values["check_preload"], "<unset>")

    def test_libpng_preload_keeps_the_callers_preload(self):
        values, _, _, libpng = self.run_loop(libpng="fake", caller_preload="/opt/other.so")
        self.assertEqual(values["preload"], libpng + ":/opt/other.so")

    def test_empty_libpng_choice_turns_the_preload_off(self):
        values, _, _, _ = self.run_loop(libpng="")
        self.assertEqual(values["preload"], "<unset>")

    def test_missing_libpng_leaves_preload_alone(self):
        values, _, _, _ = self.run_loop(libpng="/nonexistent/libpng16.so.16", caller_preload="/opt/other.so")
        self.assertEqual(values["preload"], "/opt/other.so")


if __name__ == "__main__":
    unittest.main()
