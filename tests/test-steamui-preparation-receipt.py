# SPDX-License-Identifier: MIT
"""CPU failure evidence and actual private-file publication, no GPU/process mock."""
import importlib.util,json,tempfile,unittest,subprocess
from pathlib import Path
spec=importlib.util.spec_from_file_location('prepare',Path(__file__).parents[1]/'libexec/steamui-headless-prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
class Receipt(unittest.TestCase):
    def test_sway_shell_callback_reaches_writer(self):
        profile=Path('/var/tmp/steamui/headless-'+'a'*16+'/env.json')
        line=prepare.startup_config(profile).splitlines()[-1]
        callback='/usr/bin/python3 -I /var/tmp/steamui/full-kit71h/headless-profile.py '+str(profile)
        command=line.removeprefix('exec ').replace(callback,'printf PROFILE_READY')
        result=subprocess.run(['/bin/sh','-c',command],capture_output=True,text=True,timeout=2)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,'PROFILE_READY')

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
