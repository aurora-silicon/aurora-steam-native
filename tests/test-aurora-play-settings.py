# SPDX-License-Identifier: MIT
"""Aurora Play's Stats switch writes and removes only its own Proton user_settings.py."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

PLAY = Path(__file__).parents[1] / "bin/aurora-play"
sys.dont_write_bytecode = True


def load_play(home, steam):
    # The settings code needs no GTK; stand in for gi so the module imports anywhere.
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_: None
    repo = types.ModuleType("gi.repository")
    for name in ("Adw", "Gdk", "Gio", "Gtk", "Pango"):
        setattr(repo, name, types.SimpleNamespace(ApplicationWindow=object, Application=object, Button=object))
    gi.repository = repo
    env = {"HOME": str(home), "AURORA_STEAM_ROOT": str(steam)}
    with mock.patch.dict(sys.modules, {"gi": gi, "gi.repository": repo}), mock.patch.dict(os.environ, env):
        loader = importlib.machinery.SourceFileLoader("aurora_play", str(PLAY))
        spec = importlib.util.spec_from_loader("aurora_play", loader)
        module = importlib.util.module_from_spec(spec)
        with mock.patch("pathlib.Path.home", return_value=home):
            loader.exec_module(module)
    return module


class StatsSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        steam = root / "steam"
        tools = steam / "compatibilitytools.d"
        self.ours = tools / "Aurora-Proton-10" / "user_settings.py"
        self.theirs = tools / "Aurora-Proton-11" / "user_settings.py"
        self.ours.parent.mkdir(parents=True)
        self.theirs.parent.mkdir(parents=True)
        self.own = 'user_settings = {"PROTON_LOG": "1", "MANGOHUD": "1"}\n'
        self.theirs.write_text(self.own)
        self.play = load_play(self.home, steam)

    def test_switching_on_keeps_a_users_own_settings(self):
        self.assertEqual(self.play.set_mangohud(True), 1)
        self.assertTrue(self.ours.read_text().startswith(self.play.MARKER))
        self.assertEqual(self.theirs.read_text(), self.own)

    def test_switching_off_removes_only_ours(self):
        self.play.set_mangohud(True)
        self.assertEqual(self.play.set_mangohud(False), 1)
        self.assertFalse(self.ours.exists())
        self.assertEqual(self.theirs.read_text(), self.own)

    def test_switching_on_twice_rewrites_ours(self):
        self.play.set_mangohud(True)
        self.ours.write_text(self.play.MARKER + ": an older version\n")
        self.play.set_mangohud(True)
        self.assertEqual(self.ours.read_text(), self.play.MANGOHUD_SETTINGS)


if __name__ == "__main__":
    unittest.main()
