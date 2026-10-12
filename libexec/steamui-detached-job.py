#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Detached operator bookkeeping/status/cooperative stop; no forced process actions."""
import importlib.util
import argparse,hashlib,json,os,pathlib,re,stat,subprocess,sys,time,uuid,fcntl
BASE=pathlib.Path('/var/tmp/steamui');KIT=BASE/'full-kit71h'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic(path,value):
 temp=path.with_name(path.name+'.tmp')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as f:f.write(json.dumps(value,sort_keys=True)+'\n')
 temp.replace(path)
def ticks(pid):return int((pathlib.Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19])
def boot():return pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def metadata(path,directory=False):
 s=path.lstat()
 if path.is_symlink() or s.st_uid!=0 or (s.st_mode&0o7777)!=(0o700 if directory else 0o600) or (not directory and (not stat.S_ISREG(s.st_mode) or s.st_nlink!=1)):raise RuntimeError('operator state metadata unsafe')
def load(job):
 metadata(job,True);metadata(job/'metadata.json');j=json.loads((job/'metadata.json').read_text())
 if not re.fullmatch(r'/var/tmp/steamui-full-job-\d{8}-\d{6}-[0-9a-f]{8}',str(job)):raise RuntimeError('unexpected job path')
 if not re.fullmatch(r'/var/tmp/steamui-owner-\d{8}-\d{6}-[0-9a-f]{8}\.json',j['owner_record']):raise RuntimeError('unexpected owner path')
 if not source_identity(j,job,boot(),digest(pathlib.Path(__file__).resolve())):raise RuntimeError('stale job boot/script identity')
 return j
def live(j,proc_root=pathlib.Path('/proc'),uid_of=lambda p:p.stat().st_uid):
 p=proc_root/str(j['runner_pid'])
 try:
  before=int((p/'stat').read_text().rsplit(')',1)[1].split()[19]);uid=uid_of(p);state=(p/'stat').read_text().rsplit(')',1)[1].split()[0];after=int((p/'stat').read_text().rsplit(')',1)[1].split()[19])
 except (FileNotFoundError,ProcessLookupError):return False
 if before!=after or before!=j['runner_start_ticks'] or uid!=0:raise RuntimeError('runner PID reused/identity changed')
 if state=='Z':return False
 return True
def owner(j):
 p=pathlib.Path(j['owner_record'])
 if not p.exists():return None
 metadata(p);r=json.loads(p.read_text())
 if r['boot_id']!=j['boot_id'] or r['minimum_start_ticks']<j['runner_start_ticks']:raise RuntimeError('stale owner identity')
 return r
def rows(path):
 if not path.exists():return []
 if path.is_symlink() or path.stat().st_size>64*1024*1024:raise RuntimeError('selected status file unsafe')
 text=path.read_text();lines=text.splitlines()
 if text and not text.endswith('\n'):lines=lines[:-1]
 return [json.loads(s) for s in lines if s.startswith('{')]
def status(job):
 j=load(job);r=owner(j);state=json.loads((job/'status.json').read_text()) if (job/'status.json').exists() else {'phase':'starting'}
 running=False if state.get('phase')=='finished' else live(j)
 out={'job_dir':str(job),'runner_pid':j['runner_pid'],'runner_start_ticks':j['runner_start_ticks'],'boot_id':j['boot_id'],'runner_live':running,'phase':state.get('phase'),'rc':state.get('rc'),'stop_requested':(job/'STOP.requested').exists(),'owner_record':j['owner_record'],'arms':j['arms'],'qualification_claim':False}
 if r:
  out['recorded_units']=[u['unit'] for u in r['units']];current=next((u for u in r['units'] if u['unit']==r.get('current_primary_unit')),None)
  if current:
   arm=pathlib.Path(current['arm_dir'])
   if not re.fullmatch(r'/var/tmp/steamui/res-\d{8}-\d{6}/[AC]',str(arm)):raise RuntimeError('unexpected recorded result path')
   nav=rows(arm/'nav.jsonl');soak=rows(arm/'soak.jsonl');mon=rows(arm/'mon.jsonl');starts=[x for x in soak if x.get('op')=='start' and x.get('mode')=='soak']
   out.update(current_res=str(arm.parent),arm=arm.name,nav_completed=sum(x.get('op')=='nav' for x in nav),expected_nav=10 if arm.name=='A' else 50,soak_captures=sum(x.get('op')=='soak' for x in soak),expected_soak_seconds=60 if arm.name=='A' else 600,soak_elapsed_seconds=max(0,round(time.time()-starts[0]['t'],1)) if starts and running else None)
   if mon:out['last_metrics']={k:mon[-1].get(k) for k in ['t','avail_kb','pcore_mc','swap_kb','coredump_lines','gpu_fault_lines','gate']}
 final=pathlib.Path(j['owner_record']+'.finally.json')
 if final.exists():metadata(final);out['finally_receipt']=json.loads(final.read_text())
 if not running and state.get('phase')!='finished':out['phase']='runner_exited_without_final_receipt'
 return out
def stop(job):
 j=load(job)
 state=json.loads((job/'status.json').read_text()) if (job/'status.json').exists() else {}
 if state.get('phase')=='finished' or not live(j):return {'already_finished':True,'cooperative_stop_requested':False,'original_result_unchanged':True,'no_marker_writes_or_ipc':True}
 running=True;r=owner(j)
 # Allidentity checks precede stop-marker writes/normal IPC.
 (job/'STOP.requested').touch(mode=0o600,exist_ok=True)
 marker=pathlib.Path(j['owner_record']+'.campaign.stop');fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600) if not marker.exists() else None
 if fd is not None:
  with os.fdopen(fd,'w') as f:f.write('cooperative manual handoff; no further primary/URI launches\n')
 else:metadata(marker)
 if r:
  for u in r['units']:
   if not u.get('arm_dir'):continue
   arm=pathlib.Path(u['arm_dir'])
   if not re.fullmatch(r'/var/tmp/steamui/res-\d{8}-\d{6}/[AC]',str(arm)):raise RuntimeError('unexpected owned result path')
   (arm.parent/'STOP').write_text('cooperative manual handoff; incomplete qualification\n')
  p=subprocess.run(['/usr/bin/python3',str(KIT/'owned-ui-cleanup.py'),j['owner_record'],str(job/'stop-cleanup.json')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=110)
  if p.returncode:raise RuntimeError('owned normal shutdown incomplete/foreign client preserved')
 return {'cooperative_stop_requested':True,'runner_was_live':running,'normal_shutdown_requested':r is not None,'force_kill':False,'finally_restoration_must_finish':True,'qualification_claim':False}
def restored(state):
 r=state.get('finally_receipt',{})
 return r.get('owned_shutdown_complete') is True and r.get('null_audio_stop_rc')==0 and r.get('manifest_restore_rc')==0
def launch_allowed(state):return not state.get('runner_live') and state.get('phase')=='finished' and restored(state)
def source_identity(j,job,current_boot,current_sha):return j.get('job_dir')==str(job) and j.get('boot_id')==current_boot and j.get('runner_script_sha256')==current_sha
def headless_identity(path):
 profile=pathlib.Path(path)
 if not re.fullmatch(r'/var/tmp/steamui/headless-[0-9a-f]{16}/env.json',str(profile)):
  raise RuntimeError('unexpected isolated headless profile path')
 guard_path=KIT/'session-guard.py'
 spec=importlib.util.spec_from_file_location('session_guard',guard_path)
 guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
 fd=os.open(profile,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  guard.headless_fd(fd)
  os.lseek(fd,0,os.SEEK_SET)
  with os.fdopen(os.dup(fd),'rb') as source:sha=hashlib.sha256(source.read()).hexdigest()
 finally:os.close(fd)
 return {'headless_profile':str(profile),'headless_profile_sha256':sha,'session_guard_sha256':digest(guard_path)}
def runner_environment(j):
 env={'PATH':'/usr/bin:/bin','HOME':'/root','STEAMUI_ARMS':j['arms'],'STEAMUI_OWNER_RECORD_PRESET':j['owner_record']}
 if j.get('headless_profile'):
  if digest(KIT/'session-guard.py')!=j.get('session_guard_sha256'):raise RuntimeError('headless guard source drift')
  identity=headless_identity(j['headless_profile'])
  if any(identity[key]!=j.get(key) for key in identity):raise RuntimeError('headless profile/guard identity drift')
  env['STEAMUI_HEADLESS_ENV']=identity['headless_profile']
 return env
def launch(args):
 lock=pathlib.Path('/var/tmp/steamui-full-job.active.lock');fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);st=os.fstat(fd)
 if st.st_uid!=0 or st.st_mode&0o7777!=0o600 or not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:os.close(fd);raise RuntimeError('active operator lock unsafe')
 with os.fdopen(fd,'r+') as stream:
  fcntl.flock(stream,fcntl.LOCK_EX)
  prior=stream.read()
  if prior:
   old=json.loads(prior);state=status(pathlib.Path(old['job_dir']))
   if not launch_allowed(state):raise RuntimeError('existing job live/incomplete; observe/stop it, never restart')
  result=_launch(args)
  stream.seek(0);stream.truncate();stream.write(json.dumps({'job_dir':result['job_dir']}));stream.flush()
 print(json.dumps(result))
def _launch(args):
 if args.arms not in ('A C','C'):raise RuntimeError('unsupported campaign scope')
 identity=headless_identity(args.headless_profile) if args.headless_profile else {}
 script=pathlib.Path(__file__).resolve();job=pathlib.Path('/var/tmp/steamui-full-job-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]);job.mkdir(mode=0o700)
 stamp=time.strftime('%Y%m%d-%H%M%S');preset='/var/tmp/steamui-owner-'+stamp+'-'+uuid.uuid4().hex[:8]+'.json'
 j={'job_dir':str(job),'boot_id':boot(),'runner_script_sha256':digest(script),'owner_record':preset,'arms':args.arms,'plan':args.plan,'plan_sha':args.plan_sha,'audit':args.audit,'audit_sha':args.audit_sha,'kit_outer_sha256':digest(KIT/'ui-public-scoped.sh'),'launched_t':time.time()}
 j.update(identity)
 atomic(job/'metadata.json',j);log=os.open(job/'runner.private.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 p=subprocess.Popen(['/usr/bin/python3',str(script),'run','--job',str(job)],stdout=log,stderr=log,start_new_session=True,stdin=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','HOME':'/root'});os.close(log)
 j.update(runner_pid=p.pid,runner_start_ticks=ticks(p.pid));atomic(job/'metadata.json',j)
 return {'job_dir':str(job),'runner_pid':p.pid,'runner_start_ticks':j['runner_start_ticks'],'boot_id':j['boot_id'],'owner_record':preset,'script_sha256':j['runner_script_sha256'],'detached':True,'counts_and_gates_unchanged':True,'all_future_launch_status_stop_only_via_hw_request':True}
def run(job):
 # Parent writes PID identity immediately; tolerate that short atomic registration window only.
 for _ in range(100):
  j=load(job)
  if j.get('runner_pid')==os.getpid():break
  time.sleep(.01)
 else:raise RuntimeError('runner registration failed')
 if not live(j) or digest(KIT/'ui-public-scoped.sh')!=j['kit_outer_sha256']:raise RuntimeError('runner/kit identity drift')
 atomic(job/'status.json',{'phase':'running','started_t':time.time()})
 try:
  env=runner_environment(j)
  if (job/'STOP.requested').exists():rc=2
  else:rc=subprocess.run(['/bin/bash',str(KIT/'ui-public-scoped.sh'),j['plan'],j['plan_sha'],j['audit'],j['audit_sha']],env=env).returncode
  atomic(job/'status.json',{'phase':'finished','rc':rc,'finished_t':time.time(),'cooperative_stop_requested':(job/'STOP.requested').exists(),'qualification_incomplete_if_stopped':True})
 except BaseException:
  atomic(job/'status.json',{'phase':'runner_error','rc':None,'finished_t':time.time()});raise
if __name__=='__main__':
 a=argparse.ArgumentParser();sub=a.add_subparsers(dest='op',required=True)
 l=sub.add_parser('launch');l.add_argument('--plan',required=True);l.add_argument('--plan-sha',required=True);l.add_argument('--audit',required=True);l.add_argument('--audit-sha',required=True);l.add_argument('--arms',default='A C');l.add_argument('--headless-profile')
 for n in ['run','status','stop']:sub.add_parser(n).add_argument('--job',required=True)
 v=a.parse_args()
 try:
  if os.geteuid()!=0:raise RuntimeError('operator root required')
  if v.op=='launch':launch(v)
  elif v.op=='run':run(pathlib.Path(v.job))
  else:print(json.dumps(status(pathlib.Path(v.job)) if v.op=='status' else stop(pathlib.Path(v.job)),sort_keys=True))
 except (OSError,ValueError,RuntimeError,KeyError,subprocess.SubprocessError) as e:
  print(json.dumps({'operation':v.op,'fail_closed':True,'error_category':type(e).__name__,'errno':getattr(e,'errno',None),'no_forced_actions':True,'transient_observation_error':v.op=='status','does_not_mark_job_terminal_or_restart':v.op=='status'}));raise SystemExit(2)
