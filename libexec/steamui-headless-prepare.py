#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded isolated compositor/profile/frame preparation; no Steam or seat changes."""
import argparse,hashlib,importlib.util,json,os,re,secrets,stat,struct,subprocess,tarfile,time
from pathlib import Path
BASE=Path('/var/tmp/steamui');KIT=BASE/'full-kit71h';UID=1000
USER=['runuser','-u','user','--','env','XDG_RUNTIME_DIR=/run/user/1000','DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus']
def command(argv,timeout=15):
    return subprocess.run(argv,check=True,capture_output=True,text=True,timeout=timeout).stdout.strip()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def postcheck(receipt,name,check):
    try:receipt[name]=check()
    except BaseException as error:
        receipt[name]=False;receipt[name+'_error']=type(error).__name__;receipt['pass']=False

def save_receipt(path,receipt):
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:
        json.dump(receipt,out,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
    temporary.replace(path)
    directory=os.open(path.parent,os.O_DIRECTORY);os.fsync(directory);os.close(directory)

def stage(path,expected):
    if digest(path)!=expected:raise RuntimeError('helper archive hash drift')
    if KIT.exists() or KIT.is_symlink():raise RuntimeError('kit exists; no overwrite/restart')
    parent=BASE.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or parent.st_mode&0o022:raise RuntimeError('kit parent unsafe')
    with tarfile.open(path) as tar:
        members=tar.getmembers();names=[m.name for m in members]
        if len(set(names))!=len(names):raise RuntimeError('duplicate archive member')
        for m in members:
            if not m.isfile() or len(Path(m.name).parts)!=2 or Path(m.name).parts[0]!='full-kit71h' or '..' in Path(m.name).parts:raise RuntimeError('unsafe helper member')
        KIT.mkdir(mode=0o755)
        for m in members:
            p=KIT/Path(m.name).name
            with p.open('xb') as out:out.write(tar.extractfile(m).read())
            p.chmod(0o644)
    for line in (KIT/'SHA256SUMS').read_text().splitlines():
        sha,name=line.split('  ',1)
        if Path(name).name!=name or digest(KIT/name)!=sha:raise RuntimeError('helper member hash drift')

def inactive():
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if (p/'comm').read_text().strip() in ('steam','steamwebhelper'):raise RuntimeError('existing Steam must remain stopped')
        except (FileNotFoundError,ProcessLookupError):pass

def display():
    return command(USER+['gdbus','call','--address','unix:path=/run/user/1000/bus','--dest','org.gnome.Mutter.DisplayConfig','--object-path','/org/gnome/Mutter/DisplayConfig','--method','org.gnome.Mutter.DisplayConfig.GetCurrentState'])

def execute(args):
    if os.geteuid()!=0:raise RuntimeError('root operator required')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=args.boot_id or digest(Path('/sys/kernel/notes'))!=args.notes_sha:raise RuntimeError('boot/kernel identity drift')
    if digest(Path('/var/tmp/aurora-output-gamma919.py'))!='57ed68e0131f639d09e6f4382433346cc8e37bca287928d2eaab6db820df661b':raise RuntimeError('public frame helper drift')
    inactive();before_display=display();before_taint=Path('/proc/sys/kernel/tainted').read_text()
    for service in ('speakerguardd.service','aurora-sep.service'):command(['systemctl','is-active',service])
    command(['rpm','-V','sway','wlroots','grim','mesa-dri-drivers','mesa-vulkan-drivers','mesa-libEGL'])
    cursor=command(['journalctl','-k','-n','0','--show-cursor']).split('-- cursor: ',1)[1]
    stage(Path(args.kit_tar),args.kit_sha)
    gate=module('gate',KIT/'gates.py');values=gate.sample()
    if gate.reason(*values):raise RuntimeError('existing resource gate: '+gate.reason(*values))
    guard=module('guard',KIT/'session-guard.py')
    tag=secrets.token_hex(8);token=secrets.token_hex(32);unit='aurora-steamui-headless-'+tag+'.service';pattern='aurora-steamui-pattern-'+tag+'.service'
    private=BASE/('headless-'+tag);private.mkdir(mode=0o700);os.chown(private,UID,UID)
    evidence=Path('/var/tmp/aurora-headless-prep-'+tag);evidence.mkdir(mode=0o700)
    profile=private/'env.json';config=private/'sway.conf'
    config.write_text('xwayland force\noutput * resolution 1920x1080\nexec /bin/sh -c "for i in $(seq 1 20); do /usr/bin/python3 /var/tmp/steamui/full-kit71h/headless-profile.py '+str(profile)+' && exit 0; sleep 0.25; done; exit 1"\n')
    config.chmod(0o600);os.chown(config,UID,UID)
    env={'PATH':'/usr/bin:/bin','HOME':'/home/user','XDG_RUNTIME_DIR':'/run/user/1000','DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus','WLR_BACKENDS':'headless','WLR_HEADLESS_OUTPUTS':'1','WLR_RENDERER':'gles2','MESA_LOADER_DRIVER_OVERRIDE':'zink','GALLIUM_DRIVER':'zink','LIBGL_KOPPER_DISABLE':'true','WLR_XWAYLAND':'/usr/bin/Xwayland','AURORA_HEADLESS_TOKEN':token,'AURORA_HEADLESS_UNIT':unit}
    receipt={'unit':unit,'pattern_unit':pattern,'profile':str(profile),'evidence':str(evidence),'kit_sha256':args.kit_sha,'boot_id':args.boot_id,'resource_before':values,'qualification_scope':'owned isolated preparation/capture; no Steam/GPU conformance/physical display grade','pass':False,'normal_exit':False}
    started=False;identity=None;client=None
    def unit_state():
        result=subprocess.run(USER+['systemctl','--user','show',unit,'--property=LoadState','--property=ActiveState','--property=MainPID','--property=Job'],capture_output=True,text=True,timeout=10)
        state=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode and state.get('LoadState')!='not-found':raise RuntimeError('cannot verify owned unit state')
        if state.get('LoadState')=='not-found':return dict(LoadState='not-found',ActiveState='inactive',MainPID='0',Job='')
        if any(k not in state for k in ('LoadState','ActiveState','MainPID','Job')):raise RuntimeError('incomplete owned unit state')
        return state
    try:
        state=subprocess.run(USER+['systemctl','--user','show',unit,'--property=LoadState','--value'],capture_output=True,text=True,timeout=10).stdout.strip()
        if state!='not-found':raise RuntimeError('unit already exists')
        receipt['launch_attempted']=True;receipt['ownership_token_sha256']=hashlib.sha256(token.encode()).hexdigest()
        save_receipt(evidence/'receipt.json',receipt)
        started=True
        command(USER+['systemd-run','--user','--unit='+unit,'--collect','-p','LimitCORE=0','-p','KillMode=process','/usr/bin/env','-i',*[k+'='+v for k,v in env.items()],'/usr/bin/sway','--unsupported-gpu','-c',str(config)])
        deadline=time.monotonic()+12
        while not profile.exists() and time.monotonic()<deadline:time.sleep(.1)
        j=guard.headless(profile);identity=(j['sway_pid'],j['sway_start'])
        receipt['sway_pid'],receipt['sway_start']=identity;receipt['xwayland_pid']=j['xwayland_pid'];receipt['profile_guard_pass']=True
        maps=Path('/proc',str(identity[0]),'maps').read_text()
        libraries=sorted(set(line.split()[-1] for line in maps.splitlines() if len(line.split())>=6 and ('libgallium-' in line or 'libvulkan_asahi' in line)))
        if not any('libgallium-' in p for p in libraries) or not any('libvulkan_asahi' in p for p in libraries) or any(not p.startswith('/usr/lib64/') for p in libraries):raise RuntimeError('compositor stock hardware provider maps missing')
        receipt['compositor_provider_sha256']={p:digest(Path(p)) for p in libraries}
        gui={'PATH':'/usr/bin:/bin','HOME':'/home/user','XDG_RUNTIME_DIR':str(j['runtime_dir']),'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus','GDK_BACKEND':'wayland'}
        gui.update({k:j[k] for k in ('DISPLAY','XAUTHORITY','WAYLAND_DISPLAY') if j.get(k)})
        args_env=['/usr/bin/env','-i',*[k+'='+v for k,v in gui.items()]]
        log=(evidence/'pattern.private.log').open('x');os.chmod(log.name,0o600)
        client=subprocess.Popen(USER+['systemd-run','--user','--unit='+pattern,'--collect','--wait','--pipe','-p','LimitCORE=0','-p','KillMode=process',*args_env,'/usr/bin/python3','/var/tmp/aurora-output-gamma919.py','--connector','HEADLESS-1','--seconds','8','--pattern','moving'],stdout=log,stderr=log)
        time.sleep(2);guard.headless(profile)
        png=evidence/'frame.png';command(USER+[*args_env,'/usr/bin/grim','-o','HEADLESS-1',str(private/'frame.png')]);data=(private/'frame.png').read_bytes();png.write_bytes(data);png.chmod(0o600)
        if data[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',data[16:24])!=(1920,1080):raise RuntimeError('capture dimensions/signature fail')
        rc=client.wait(timeout=15);log.close();receipt['pattern_rc']=rc;receipt['frame_sha256']=digest(png)
        if rc:raise RuntimeError('owned public test pattern failed')
        results=[json.loads(line) for line in (evidence/'pattern.private.log').read_text().splitlines() if line.startswith('{')]
        if len(results)!=1 or results[0].get('actual_connector')!='HEADLESS-1' or not results[0].get('target_pass') or not results[0].get('draw_pass'):raise RuntimeError('test pattern feedback/placement failed')
        receipt['pattern_result']=results[0];receipt['frame_review_pending']=True
        after=gate.sample();receipt['resource_after']=after
        if gate.reason(*after):raise RuntimeError('post-preparation resource gate failed')
        receipt['pass']=True
    except BaseException as error:
        receipt['error']=type(error).__name__;receipt['error_rc']=getattr(error,'returncode',None)
    finally:
        if client is not None and client.poll() is None:
            try:client.wait(timeout=15)
            except subprocess.TimeoutExpired:receipt['pattern_still_live']=True;receipt['pass']=False
        if started:
            try:
                deadline=time.monotonic()+15;exit_sent=False
                while time.monotonic()<deadline:
                    state=unit_state();main=int(state['MainPID'])
                    if main and not exit_sent:
                        p=guard.process(main)
                        if p['uid']!=UID or p['comm']!='sway' or p['env'].get(b'AURORA_HEADLESS_TOKEN')!=token.encode() or guard.unit_group(p,unit) is None or (identity and (main,p['start'])!=identity):raise RuntimeError('cleanup compositor identity changed')
                        ipc=Path('/run/user/1000')/f'sway-ipc.{UID}.{main}.sock'
                        try:
                            guard.owned_socket(ipc,main,Path('/proc'),UID)
                            command(USER+['swaymsg','-s',str(ipc),'exit']);exit_sent=True
                        except (FileNotFoundError,ConnectionRefusedError):pass
                        except subprocess.CalledProcessError:receipt['exit_reply_ambiguous']=True
                    live=[]
                    for process in Path('/proc').glob('[0-9]*'):
                        try:
                            if any(line.startswith('0::') and Path(line[3:]).name in (unit,pattern) for line in (process/'cgroup').read_text().splitlines()):live.append(int(process.name))
                        except (FileNotFoundError,ProcessLookupError):pass
                    if not live and (state['LoadState']=='not-found' or state['ActiveState'] in ('inactive','failed') and state['Job'] in ('','0')):
                        receipt['normal_exit']=True;break
                    time.sleep(.1)
                if not receipt['normal_exit']:raise RuntimeError('owned unit/descendants remain; no forced stop or launch retry')
            except BaseException as error:receipt['cleanup_error']=str(error);receipt['pass']=False
        postcheck(receipt,'protective_services_active',lambda:all(command(['systemctl','is-active',service])=='active' for service in ('speakerguardd.service','aurora-sep.service')))
        postcheck(receipt,'physical_state_unchanged',lambda:display()==before_display)
        postcheck(receipt,'taint_unchanged',lambda:Path('/proc/sys/kernel/tainted').read_text()==before_taint)
        def kernel_check():
            journal=command(['journalctl','-k','--after-cursor='+cursor,'--no-pager']);log=evidence/'kernel.private.log';log.write_text(journal);log.chmod(0o600)
            errors=[line for line in journal.splitlines() if re.search(r'BUG:|WARNING:|Oops|Call Trace|(?:asahi|apple-dcp|drm).*?(?:fault|error|timeout|failed)',line,re.I)]
            receipt['matching_new_kernel_errors']=len(errors)
            return not errors
        postcheck(receipt,'kernel_errors_absent',kernel_check)
        if not all(receipt.get(k) for k in ('normal_exit','physical_state_unchanged','taint_unchanged','protective_services_active','kernel_errors_absent')):receipt['pass']=False
        save_receipt(evidence/'receipt.json',receipt)
    print(json.dumps(receipt,sort_keys=True))
    return 0 if receipt['pass'] else 2
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('boot-id','notes-sha','kit-tar','kit-sha'):parser.add_argument('--'+name,required=True)
    raise SystemExit(execute(parser.parse_args()))
