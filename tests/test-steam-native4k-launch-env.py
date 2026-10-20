# SPDX-License-Identifier: MIT
"""The launch loop's process contract: open-file limit, FEX graphics provider and the exported
userpages launcher."""
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import unittest

LOOP = Path(__file__).parents[1] / "libexec/steam-native4k-launch-loop.sh"


class LaunchEnvironment(unittest.TestCase):
    def run_loop(self, provider_exists=True, caller_provider=None, soft_nofile=None, launcher_from_path=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steam/steamrtarm64/steam"
            steam.parent.mkdir(parents=True)
            steam.write_text("#!/bin/sh\nexit 0\n"); steam.chmod(0o755)
            report = root / "report"
            # Stands in for userpages: record what Steam would inherit, then stop.
            launcher = root / "userpages"
            launcher.write_text("#!/bin/bash\n{ echo \"nofile=$(ulimit -Sn) $(ulimit -Hn)\";"
                                " echo \"provider=${STEAM_COMPAT_GRAPHICS_PROVIDER-<unset>}\";"
                                " echo \"launcher=${AURORA_USERPAGES_LAUNCHER-<unset>}\"; } > \"$REPORT\"\n")
            launcher.chmod(0o755)
            provider = root / "aurora-fex-mesa/graphics_provider.json"
            if provider_exists:
                provider.parent.mkdir()
                provider.write_text("{}\n")
            env = {"PATH": os.environ["PATH"], "HOME": str(root), "REPORT": str(report),
                   "AURORA_STEAM_ROOT": str(root / "steam"), "AURORA_USERPAGES_LAUNCHER": str(launcher),
                   "AURORA_FEX_GRAPHICS_PROVIDER": str(provider),
                   "AURORA_STEAM_CONSOLE_LOG": str(root / "console.log")}
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
            return values, str(provider), str(root / "bin/userpages")

    def test_installed_aurora_provider_is_exported(self):
        values, provider, _ = self.run_loop()
        self.assertEqual(values["provider"], provider)

    def test_caller_choice_of_provider_wins(self):
        values, _, _ = self.run_loop(caller_provider="/elsewhere/graphics_provider.json")
        self.assertEqual(values["provider"], "/elsewhere/graphics_provider.json")

    def test_absent_provider_leaves_steam_default(self):
        values, _, _ = self.run_loop(provider_exists=False)
        self.assertEqual(values["provider"], "<unset>")

    def test_soft_open_file_limit_is_raised_to_the_hard_limit(self):
        hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if hard != resource.RLIM_INFINITY and hard <= 256:
            self.skipTest("hard open-file limit too low to observe a raise")
        values, _, _ = self.run_loop(soft_nofile=256)
        soft, reported_hard = values["nofile"].split()
        self.assertEqual(soft, reported_hard)
        self.assertNotEqual(soft, "256")

    def test_userpages_launcher_is_exported_to_steam_children(self):
        # Proton tools launched by Steam read AURORA_USERPAGES_LAUNCHER from their environment.
        values, _, found = self.run_loop(launcher_from_path=True)
        self.assertEqual(values["launcher"], found)


if __name__ == "__main__":
    unittest.main()
