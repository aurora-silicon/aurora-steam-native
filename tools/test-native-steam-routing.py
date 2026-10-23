#!/usr/bin/env python3
"""Offline public CLI contract; fixtures never installed or used as Steam APIs."""
import json,os,pathlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class Routing(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.home=pathlib.Path(self.temp.name);self.client=self.home/'client with spaces';(self.client/'steamrtarm64').mkdir(parents=True);(self.client/'steamrtarm64/steam').write_text('not executed offline fixture');(self.client/'steamrtarm64/steam').chmod(0o755);(self.home/'.steam').mkdir();(self.home/'.steam/root').symlink_to(self.client)
  self.record=self.home/'record';self.record.mkdir();(self.record/'steam-native4k-launch-loop.sh').write_text('#!/bin/bash\n/usr/bin/python3 -c \'import json,os,sys;print(json.dumps(dict(helper=os.environ.get("AURORA_USERPAGES_LAUNCHER"),root=os.environ.get("AURORA_STEAM_ROOT"),args=sys.argv[1:])))\' "$@"\n');(self.record/'steam-native4k-launch-loop.sh').chmod(0o755)
 def tearDown(self):self.temp.cleanup()
 def env(self,**extra):
  e={k:v for k,v in os.environ.items() if k not in ('AURORA_USERPAGES_LAUNCHER','AURORA_STEAM_ROOT','AURORA_STEAM_LIBEXEC')};e.update(HOME=str(self.home),AURORA_STEAM_LIBEXEC=str(self.record),**extra);return e
 def test_default_standard_and_current_root(self):
  r=subprocess.run([str(ROOT/'bin/steam-native4k'),'steam://open/games','arg with spaces'],env=self.env(),capture_output=True,text=True,check=True);d=json.loads(r.stdout);self.assertEqual(d['helper'],'/usr/libexec/aurora/userpages');self.assertEqual(d['root'],str(self.client.resolve()));self.assertEqual(d['args'],['steam://open/games','arg with spaces'])
 def test_explicit_reviewed_override(self):
  r=subprocess.run([str(ROOT/'bin/steam-native4k')],env=self.env(AURORA_USERPAGES_LAUNCHER='/reviewed/helper'),capture_output=True,text=True,check=True);self.assertEqual(json.loads(r.stdout)['helper'],'/reviewed/helper')
 def test_direct_loop_exports_shell_local_selection(self):
  helper=self.home/'helper';helper.write_text('#!/usr/bin/python3\nimport os\nprint(os.environ.get("AURORA_USERPAGES_LAUNCHER","MISSING"))\n');helper.chmod(0o755);log=self.home/'loop.log'
  env=self.env(AURORA_STEAM_ROOT=str(self.client),AURORA_STEAM_CONSOLE_LOG=str(log))
  # Shell-local value is not exported by the caller; the public loop must export it.
  r=subprocess.run(['/bin/bash','-c','AURORA_USERPAGES_LAUNCHER="$1"; source "$2"','offline',str(helper),str(ROOT/'libexec/steam-native4k-launch-loop.sh')],env=env,capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr);self.assertIn(str(helper),log.read_text());self.assertNotIn('MISSING',log.read_text())
 def test_loop_missing_helper_fails_before_client(self):
  r=subprocess.run([str(ROOT/'libexec/steam-native4k-launch-loop.sh')],env=self.env(AURORA_STEAM_ROOT=str(self.client),AURORA_USERPAGES_LAUNCHER=str(self.home/'missing')),capture_output=True,text=True);self.assertEqual(r.returncode,2);self.assertIn('launcher not found',r.stderr)
if __name__=='__main__':unittest.main()
