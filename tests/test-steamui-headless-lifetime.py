# SPDX-License-Identifier: MIT
"""CPU restoration/lifetime policy only; no hardware or UI mock."""
import importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('campaign',Path(__file__).parents[1]/'libexec/steamui-headless-campaign.py')
campaign=importlib.util.module_from_spec(spec);spec.loader.exec_module(campaign)
class Lifetime(unittest.TestCase):
    def test_terminal_restoration_requires_every_proof(self):
        final={'owned_shutdown_complete':True,'null_audio_stop_rc':0,'manifest_restore_rc':0}
        good={'phase':'finished','runner_live':False,'finally_receipt':final}
        self.assertTrue(campaign.restored_terminal(good))
        for state in [dict(good,phase='running'),dict(good,runner_live=True),dict(good,finally_receipt={}),dict(good,finally_receipt=dict(final,owned_shutdown_complete=False)),dict(good,finally_receipt=dict(final,null_audio_stop_rc=2)),dict(good,finally_receipt=dict(final,manifest_restore_rc=2))]:self.assertFalse(campaign.restored_terminal(state))
if __name__=='__main__':unittest.main()
