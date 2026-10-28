#!/usr/bin/env python3
"""Offline CLI contract checks. Fixtures are never installed into a runtime."""
import json,os,pathlib,shutil,subprocess,tempfile,unittest
WRAPPER=pathlib.Path(__file__).resolve().parents[1]/'proton'/'aurora-proton'
class Contract(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.tool=pathlib.Path(self.temp.name)/'tool with spaces';self.tool.mkdir();shutil.copy2(WRAPPER,self.tool/'aurora-proton')
  bin=self.tool/'files/bin-arm64';bin.mkdir(parents=True)
  for name in ('wine','wineserver'):(bin/name).write_text('unused offline fixture');(bin/name).chmod(0o755)
  (self.tool/'proton').write_text('import json,os,sys\nprint(json.dumps(dict(args=sys.argv[1:],arm=os.environ.get("PROTON_USE_ARM64"),server=os.environ.get("WINESERVER"),marker=os.environ.get("CONTRACT_MARKER"),wayland=os.environ.get("PROTON_ENABLE_WAYLAND"))))\n')
  self.runner=pathlib.Path(self.temp.name)/'offline runner';self.runner.write_text('#!/bin/bash\n[ "$1" = 4k ] || exit 99\nshift\nexec "$@"\n');self.runner.chmod(0o755)
 def tearDown(self):self.temp.cleanup()
 def run_cli(self,*args,**extra):
  env=dict(os.environ,AURORA_USERPAGES_LAUNCHER=str(self.runner),CONTRACT_MARKER='inherited');env.pop('PROTON_ENABLE_WAYLAND',None);env.update(extra)
  return subprocess.run([str(self.tool/'aurora-proton'),*args],env=env,capture_output=True,text=True)
 def test_wayland_default_on(self):
  r=self.run_cli('run');self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['wayland'],'1')
 def test_wayland_opt_out_kept(self):
  r=self.run_cli('run',PROTON_ENABLE_WAYLAND='0');self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['wayland'],'0')
 def test_cli_and_spaces(self):
  r=self.run_cli('run','path with spaces.exe','literal $value');self.assertEqual(r.returncode,0,r.stderr);d=json.loads(r.stdout);self.assertEqual(d['args'],['run','path with spaces.exe','literal $value']);self.assertEqual(d['arm'],'1');self.assertEqual(d['server'],str((self.tool/'files/bin-arm64/wineserver').resolve()));self.assertEqual(d['marker'],'inherited')
 def test_missing_native_component(self):
  (self.tool/'files/bin-arm64/wine').unlink();self.assertEqual(self.run_cli('run').returncode,2)
 def test_non_executable_native_component(self):
  (self.tool/'files/bin-arm64/wineserver').chmod(0o644);self.assertEqual(self.run_cli('run').returncode,2)
 def test_missing_public_runner(self):
  self.runner.unlink();self.assertEqual(self.run_cli('run').returncode,2)
if __name__=='__main__':unittest.main()
