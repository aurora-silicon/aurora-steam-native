# SPDX-License-Identifier: MIT
"""CPU failure evidence and actual private-file publication, no GPU/process mock."""
import importlib.util,json,tempfile,unittest,subprocess
from pathlib import Path
spec=importlib.util.spec_from_file_location('prepare',Path(__file__).parents[1]/'libexec/steamui-headless-prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
class Receipt(unittest.TestCase):
    def test_restored_failed_stopped_or_expired_work_cannot_pass(self):
        good={'pass':True,'launch_sender_settled':True,'normal_exit':True,'physical_state_unchanged':True,'taint_unchanged':True,'protective_services_active':True,'kernel_errors_absent':True,'ui_launch_attempted':True,'ui_restoration_complete':True}
        self.assertTrue(prepare.final_grade(good))
        for reason in ('UI rc2','cooperative stop','watchdog expiry'):
            receipt=dict(good)
            prepare.record_failure(receipt,RuntimeError(reason))
            self.assertTrue(prepare.compositor_may_close(receipt))
            self.assertFalse(prepare.final_grade(receipt))
            self.assertFalse(receipt['pass'])

    def test_incomplete_ui_cannot_close_compositor(self):
        self.assertTrue(prepare.compositor_may_close({}))
        self.assertFalse(prepare.compositor_may_close({'ui_launch_attempted':True}))
        self.assertFalse(prepare.compositor_may_close({'ui_launch_attempted':True,'ui_restoration_complete':False}))
        self.assertTrue(prepare.compositor_may_close({'ui_launch_attempted':True,'ui_restoration_complete':True}))

    def test_sway_shell_callback_reaches_candidate_collector(self):
        profile=Path('/var/tmp/steamui/headless-'+'a'*16+'/env.json')
        line=prepare.startup_config(profile).splitlines()[-1]
        command=line.removeprefix('exec ')
        import shlex
        parsed=shlex.split(command)
        self.assertEqual(parsed[:3],['/usr/bin/python3','-I','-c'])
        self.assertEqual(parsed[3],prepare.CANDIDATE_CODE)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'candidate.json'
            result=subprocess.run(['/bin/sh','-c',command.replace(str(profile.with_name('candidate.json')),str(target))],capture_output=True,text=True,timeout=2)
            self.assertEqual(result.returncode,0)
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            self.assertIn('WAYLAND_DISPLAY',json.loads(target.read_text()))
    def test_failed_postcheck_keeps_failure_and_allows_later_evidence(self):
        receipt={'pass':True,'unit':'owned','normal_exit':True}
        def fail():raise RuntimeError('private diagnostic')
        prepare.postcheck(receipt,'physical_state_unchanged',fail)
        prepare.postcheck(receipt,'kernel_errors_absent',lambda:True)
        self.assertFalse(receipt['pass']);self.assertFalse(receipt['physical_state_unchanged'])
        self.assertEqual(receipt['physical_state_unchanged_error'],'RuntimeError')
        self.assertTrue(receipt['kernel_errors_absent']);self.assertEqual(receipt['unit'],'owned')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'receipt.json';prepare.save_receipt(path,receipt)
            self.assertEqual(json.loads(path.read_text()),receipt)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            receipt['cleanup_error']='retained';prepare.save_receipt(path,receipt)
            self.assertEqual(json.loads(path.read_text()),receipt)
            self.assertEqual(list(Path(directory).iterdir()),[path])
if __name__=='__main__':unittest.main()
