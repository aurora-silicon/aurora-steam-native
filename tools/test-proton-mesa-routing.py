#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright 2026 Aurora Silicon
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HELPER = Path(__file__).with_name('proton-mesa-routing.py')
spec = importlib.util.spec_from_file_location('routing', HELPER)
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)
OLD = b'#!/bin/bash\nset -euo pipefail\nexport LD_LIBRARY_PATH="/legacy/mesa/lib:${LD_LIBRARY_PATH:-}"\nexport LIBGL_DRIVERS_PATH=/legacy/mesa/lib/dri\nexport VK_DRIVER_FILES=/legacy/mesa/icd.json\nexport OTHER=keep\nexec "$TOOL/proton" "$@"\n'
NEW = b'#!/bin/bash\nset -euo pipefail\nexport OTHER=keep\nexec "$TOOL/proton" "$@"\n'


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wrapper = self.root / 'tool' / 'aurora-proton'
        self.wrapper.parent.mkdir()
        self.wrapper.write_bytes(OLD)
        self.wrapper.chmod(0o751)
        self.record = self.root / 'transaction.json'
        self.before, self.after = routing.digest(OLD), routing.digest(NEW)

    def cli(self, mode, expected=None, record=None, wrapper=None):
        args = [sys.executable, str(HELPER), mode, '--wrapper', str(wrapper or self.wrapper)]
        if expected: args += ['--expected-sha256', expected]
        if mode in ('plan', 'apply'): args += ['--retire-prefix', '/legacy/mesa']
        if record: args += ['--record', str(record)]
        return subprocess.run(args, capture_output=True, text=True)

    def test_exact_mesa_edit_and_other_paths(self):
        result, changes = routing.transform(OLD, ['/legacy/mesa'])
        self.assertEqual(result, NEW)
        self.assertEqual(len(changes), 3)
        result, _ = routing.transform(b'export LD_LIBRARY_PATH="/legacy/mesa/lib:/public/lib:${LD_LIBRARY_PATH:-}"\n', ['/legacy/mesa'])
        self.assertEqual(result, b'export LD_LIBRARY_PATH="/public/lib:${LD_LIBRARY_PATH:-}"\n')

    def test_unknown_vars_and_shell_expressions_refused(self):
        for data in (b'export OTHER=/legacy/mesa/lib\n', b'export LD_LIBRARY_PATH="$(echo /legacy/mesa/lib)"\n',
                     b'export LD_LIBRARY_PATH="${PREFIX:-/legacy/mesa/lib}"\n', b'export LD_LIBRARY_PATH=/legacy/mesa/lib; echo extra\n'):
            with self.subTest(data=data), self.assertRaises(ValueError): routing.transform(data, ['/legacy/mesa'])

    def test_ambiguous_prefix_refused(self):
        for value in ('/', '/opt', '/opt/../', '/legacy/mesa*'):
            with self.subTest(value=value), self.assertRaises(ValueError): routing.prefixes([value])

    def test_sha_lock(self):
        self.assertEqual(self.cli('apply', '0' * 64, self.record).returncode, 2)
        self.assertEqual(self.wrapper.read_bytes(), OLD)
        self.assertFalse(self.record.exists())

    def test_plan_apply_rollback_metadata(self):
        plan = self.cli('plan', self.before)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)['after_sha256'], self.after)
        self.assertEqual(self.wrapper.read_bytes(), OLD)
        applied = self.cli('apply', self.before, self.record)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(self.wrapper.read_bytes(), NEW)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o751)
        backup = Path(json.loads(self.record.read_text())['backup'])
        self.assertEqual(backup.read_bytes(), OLD)
        restored = self.cli('rollback', self.after, self.record)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.wrapper.read_bytes(), OLD)

    def test_stale_rollback_and_noop_repeat(self):
        self.assertEqual(self.cli('apply', self.before, self.record).returncode, 0)
        unused = self.root / 'unused.json'
        again = self.cli('apply', self.after, unused)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertFalse(unused.exists())
        self.wrapper.write_bytes(NEW + b'\n')
        self.assertEqual(self.cli('rollback', self.after, self.record).returncode, 2)

    def test_link_refusal(self):
        link = self.root / 'symlink'; link.symlink_to(self.wrapper)
        self.assertEqual(self.cli('inspect', wrapper=link).returncode, 2)
        hard = self.root / 'hardlink'; os.link(self.wrapper, hard)
        self.assertEqual(self.cli('inspect').returncode, 2)

    @unittest.skipUnless(Path('/proc').is_dir(), 'Linux process inventory')
    def test_active_tool_refusal(self):
        child = subprocess.Popen(['sleep', '30'], cwd=self.wrapper.parent)
        self.addCleanup(lambda: child.poll() is None and child.terminate())
        try:
            result = self.cli('apply', self.before, self.record)
            self.assertEqual(result.returncode, 2)
            self.assertIn('in use', result.stderr)
            self.assertEqual(self.wrapper.read_bytes(), OLD)
            self.assertFalse(self.record.exists())
        finally:
            child.terminate(); child.wait()

    @unittest.skipUnless(Path('/proc').is_dir(), 'Linux process inventory')
    def test_interpreted_launcher_outside_tool_cwd(self):
        script = self.wrapper.parent / 'proton'
        script.write_text('import time; time.sleep(30)')
        child = subprocess.Popen([sys.executable, str(script)], cwd=self.root)
        try:
            result = self.cli('apply', self.before, self.record)
            self.assertEqual(result.returncode, 2)
            self.assertIn('in use', result.stderr)
            self.assertEqual(self.wrapper.read_bytes(), OLD)
            self.assertFalse(self.record.exists())
        finally:
            child.terminate(); child.wait()

    def test_artifact_audit(self):
        artifact = self.wrapper.parent / 'files' / 'ntdll.so'
        artifact.parent.mkdir(); artifact.write_bytes(b'fixture')
        lock = self.root / 'lock.json'
        lock.write_text(json.dumps({'historical_artifact_sha256': {'files/ntdll.so': hashlib.sha256(b'fixture').hexdigest()}}))
        args = [sys.executable, str(HELPER), 'audit', '--wrapper', str(self.wrapper), '--source-lock', str(lock)]
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 0)
        artifact.write_bytes(b'changed')
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 2)


if __name__ == '__main__': unittest.main()
