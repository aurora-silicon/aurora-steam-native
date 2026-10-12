# SPDX-License-Identifier: MIT
"""CPU bookkeeping/routing only; no real compositor or process launch."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('job',Path(__file__).parents[1]/'libexec/steamui-detached-job.py')
job=importlib.util.module_from_spec(spec);spec.loader.exec_module(job)

class HeadlessAdmission(unittest.TestCase):
    def test_profile_forwarded_from_explicit_receipt_only(self):
        identity=dict(headless_profile='/var/tmp/steamui/headless-'+'a'*16+'/env.json',headless_profile_sha256='profile',session_guard_sha256='guard')
        receipt=dict(identity,arms='A C',owner_record='/var/tmp/owned.json')
        with patch.object(job,'digest',return_value='guard'),patch.object(job,'headless_identity',return_value=identity):
            env=job.runner_environment(receipt)
        self.assertEqual(env['STEAMUI_HEADLESS_ENV'],identity['headless_profile'])
        self.assertEqual(set(env),{'PATH','HOME','STEAMUI_ARMS','STEAMUI_OWNER_RECORD_PRESET','STEAMUI_HEADLESS_ENV'})
        with patch.object(job,'headless_identity',side_effect=AssertionError('implicit admission')):
            self.assertNotIn('STEAMUI_HEADLESS_ENV',job.runner_environment(dict(arms='C',owner_record='/var/tmp/owned.json')))
        for key in ['headless_profile_sha256','session_guard_sha256']:
            with self.subTest(key=key),patch.object(job,'digest',return_value='guard'),patch.object(job,'headless_identity',return_value=dict(identity,**{key:'changed'})):
                with self.assertRaises(RuntimeError):job.runner_environment(receipt)
    def test_invalid_or_stale_identity_never_falls_back(self):
        for path in ['/run/user/1000/env.json','/var/tmp/steamui/headless-x/env.json','/var/tmp/steamui/headless-'+'a'*16+'/../env.json']:
            with self.subTest(path=path),self.assertRaises(RuntimeError):job.headless_identity(path)
        with patch.object(job,'digest',return_value='guard'),patch.object(job,'headless_identity',side_effect=RuntimeError('stale')):
            with self.assertRaises(RuntimeError):job.runner_environment(dict(arms='A C',owner_record='owned',headless_profile='stale',session_guard_sha256='guard'))
    def test_original_terminal_and_restoration_guards(self):
        finished=dict(runner_live=False,phase='finished',finally_receipt=dict(owned_shutdown_complete=True,null_audio_stop_rc=0,manifest_restore_rc=0))
        self.assertTrue(job.launch_allowed(finished))
        for variant in [dict(finished,runner_live=True),dict(finished,phase='starting'),dict(finished,phase='runner_exited_without_final_receipt'),dict(finished,finally_receipt={})]:self.assertFalse(job.launch_allowed(variant))

if __name__=='__main__':unittest.main()
