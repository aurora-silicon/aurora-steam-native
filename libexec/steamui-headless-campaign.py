#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hold a fully validated owned compositor through the unchanged full A/C job."""
import argparse,hashlib,importlib.util,json,os,stat,subprocess,time
from pathlib import Path

def verified_module(name,path,expected):
    path=Path(path);st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise RuntimeError('operator source/metadata mismatch')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
    return module

def restored_terminal(state):
    final=state.get('finally_receipt',{})
    return state.get('phase')=='finished' and state.get('runner_live') is False and final.get('owned_shutdown_complete') is True and final.get('null_audio_stop_rc')==0 and final.get('manifest_restore_rc')==0

def ui_work(args,prepare,operator):
    def work(profile,evidence,guard,receipt):
        guard.headless(profile)
        receipt['ui_launch_attempted']=True;receipt['ui_restoration_complete']=False
        prepare.save_receipt(evidence/'receipt.json',receipt)
        log=evidence/'ui-launch.private.log'
        with log.open('x') as output:
            os.chmod(log,0o600)
            sender=subprocess.Popen(['/usr/bin/python3','-I',args.operator,'launch','--plan',args.plan,'--plan-sha',args.plan_sha,'--audit',args.audit,'--audit-sha',args.audit_sha,'--arms','A C','--headless-profile',str(profile)],stdin=subprocess.DEVNULL,stdout=output,stderr=output)
            receipt['ui_launch_sender_pid']=sender.pid;prepare.save_receipt(evidence/'receipt.json',receipt)
            try:rc=sender.wait(timeout=30)
            except subprocess.TimeoutExpired:
                receipt['ui_launch_sender_unresolved']=True;raise RuntimeError('UI launch reply unresolved; compositor retained')
        if rc:raise RuntimeError('UI launch refused; inspect existing operator state, no relaunch')
        lines=[json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
        if len(lines)!=1 or not lines[0].get('detached'):raise RuntimeError('UI launch receipt missing; compositor retained')
        job=Path(lines[0]['job_dir']);receipt['ui_job']=str(job);prepare.save_receipt(evidence/'receipt.json',receipt)
        deadline=time.monotonic()+1500;stop_sent=False;timed_out=False
        while True:
            state=operator.status(job)
            summary={k:state.get(k) for k in ('phase','runner_live','rc','arm','nav_completed','expected_nav','soak_elapsed_seconds','stop_requested','last_metrics')}
            prepare.save_receipt(evidence/'ui-status.json',summary)
            if restored_terminal(state):
                receipt['ui_restoration_complete']=True;receipt['ui_rc']=state.get('rc');prepare.save_receipt(evidence/'receipt.json',receipt)
                if timed_out or state.get('stop_requested') or state.get('rc')!=0:raise RuntimeError('full UI failed/stopped; original result retained')
                receipt['full_ui_operator_rc0']=True
                receipt['steam_frame_review_pending']=True
                return
            if not state.get('runner_live'):
                raise RuntimeError('UI ended without terminal restoration proof; compositor retained')
            if time.monotonic()>=deadline:
                if stop_sent:raise RuntimeError('UI cooperative shutdown/restoration unresolved; compositor retained')
                operator.stop(job);stop_sent=True;timed_out=True;deadline=time.monotonic()+150
            guard.headless(profile)
            time.sleep(2)
    return work

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('boot-id','notes-sha','kit-tar','kit-sha','prepare','prepare-sha','operator','operator-sha','plan','plan-sha','audit','audit-sha'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--reuse-kit',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('root campaign required')
    prepare=verified_module('prepare',args.prepare,args.prepare_sha)
    operator=verified_module('operator',args.operator,args.operator_sha)
    raise SystemExit(prepare.execute(args,work=ui_work(args,prepare,operator)))
