#!/usr/bin/env python3
"""Pull CI-approved public source on the private server; preserve private data.

Run by the same Unix user as the teaching services. No SSH key, cloud token,
uploaded PDF or account database is added to GitHub by this updater.
"""
from __future__ import print_function
import argparse
import ast
import fcntl
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
REPO='Wayne59882/multi-course-virtual-human-ta'
PRESERVE={'wrangler.jsonc','reference_corpus/chunks.jsonl','reference_corpus/corpus_meta.json'}
# Public address named after the thesis: https://multicourse-ta.<account>.workers.dev.
# A small Worker with this name passes every request to the existing teaching Worker
# (service binding), which keeps its tunnel origin and settings; the old address then
# forwards visitors to the new one.
SITE_WORKER='multicourse-ta'
SITE_RETRY_SECONDS=1800

def run(args,**kwargs):
    return subprocess.check_output(args,cwd=str(ROOT),**kwargs).decode().strip()

def load_config(path,target):
    text=Path(path).read_text(encoding='utf-8-sig')
    text=re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',lambda m:m.group() if m.group().startswith('"') else '',text)
    text=re.sub(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])',lambda m:m.group() if m.group().startswith('"') else '',text)
    cfg=json.loads(text)
    if not cfg.get('name') or not isinstance(cfg.get('assets'),dict):raise RuntimeError('Existing Worker configuration is incomplete')
    cfg.pop('vars',None);cfg['keep_vars']=True
    cfg['main']=str(target/'cloudflare/worker/src/index.js')
    cfg['assets']['directory']=str(target/'web')
    return cfg

def deploy_worker(target,cfg,extra=()):
    """Deploy one Worker configuration with wrangler; return its output."""
    with tempfile.NamedTemporaryFile(mode='w',suffix='.json',dir=str(target),delete=False) as file:json.dump(cfg,file);config_path=file.name
    try:
        try:output=subprocess.check_output(['npx','--yes','wrangler@4.127.0','deploy','--config',config_path]+list(extra),cwd=str(target),stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as error:
            raise RuntimeError('wrangler deploy of '+str(cfg.get('name'))+' failed: '+(error.output or b'').decode('utf-8','replace')[-600:])
        output=output.decode('utf-8','replace');print(output,flush=True);return output
    finally:os.unlink(config_path)

def site_answers(url,timeout=15):
    """True: the new address serves the site. False: it answers with something else.
    None: this server cannot reach workers.dev (its DNS may not resolve it)."""
    request=urllib.request.Request(url.rstrip('/')+'/login/',headers={'User-Agent':'MC-VHTA-site-check'})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:return response.status==200
    except urllib.error.HTTPError:return False
    except (urllib.error.URLError,OSError):return None

def move_site(target,cfg,deploy=None,check=None,wait=time.sleep):
    """Give the site its thesis address; the old address forwards there. Returns the new URL."""
    deploy=deploy or deploy_worker;check=check or site_answers
    old=cfg['name']
    if old==SITE_WORKER:return None
    proxy={'name':SITE_WORKER,'main':str(target/'cloudflare/worker/src/site-proxy.js'),'compatibility_date':cfg.get('compatibility_date') or '2026-08-28',
           'workers_dev':True,'services':[{'binding':'APP','service':old}]}
    if cfg.get('account_id'):proxy['account_id']=cfg['account_id']
    match=re.search(r'https://'+re.escape(SITE_WORKER)+r'\.[a-z0-9-]+\.workers\.dev',deploy(target,proxy,[]))
    if not match:raise RuntimeError('wrangler did not report the new address')
    site=match.group(0);result=None
    for _ in range(12):  # a new workers.dev address can take a minute to answer
        result=check(site)
        if result is not False:break
        wait(5)
    if result is False:raise RuntimeError(site+' does not serve the site yet')
    # Only now does the old address start forwarding (kept by later --keep-vars deploys).
    deploy(target,cfg,['--keep-vars','--var','SITE_URL:'+site])
    (target/'logs'/'site_url.txt').write_text(site+'\n')
    print('SITE ADDRESS: '+site+' (the old address forwards there)',flush=True)
    return site

def site_step(target,cfg,previous):
    """State fields for the site address. Never raises: on failure the old address keeps serving."""
    if previous.get('site_url'):return {'site_url':previous['site_url']}
    try:site=move_site(target,cfg)
    except Exception as error:
        print('SITE ADDRESS UNCHANGED: '+str(error)[:400]+' Retrying later.',flush=True)
        return {'site_pending':True,'site_attempt_at':time.time()}
    return {'site_url':site} if site else {}

def ci_passed(sha):
    url='https://api.github.com/repos/'+REPO+'/actions/runs?event=push&head_sha='+sha+'&per_page=30'
    request=urllib.request.Request(url,headers={'User-Agent':'MC-VHTA-private-server-sync'})
    with urllib.request.urlopen(request,timeout=20) as response:data=json.loads(response.read().decode())
    runs=[row for row in data.get('workflow_runs',[]) if row.get('name')=='Offline checks' and row.get('head_sha')==sha]
    return bool(runs and runs[0].get('status')=='completed' and runs[0].get('conclusion')=='success')

def snapshot(target,name,required=True):
    target=Path(target).resolve();candidates=[]
    try:
        own=next(line.split()[1:] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('NSpid:'))
        depth=len(own)
    except (OSError,StopIteration):depth=1
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            if proc.stat().st_uid!=os.getuid():continue
            status=(proc/'status').read_text()
            namespace_ids=next((line.split()[1:] for line in status.splitlines() if line.startswith('NSpid:')),[proc.name])
            if len(namespace_ids)<depth:continue
            signal_pid=int(namespace_ids[depth-1])
            cmd=[os.fsdecode(x) for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
            indices=[i for i,value in enumerate(cmd) if Path(value).name==name+'.py']
            if len(indices)!=1:continue
            cwd=Path(os.readlink(str(proc/'cwd'))).resolve()
            index=indices[0];argument=Path(cmd[index])
            expected=(target/(name+'.py')).resolve()
            if argument.is_absolute():
                if argument.resolve()!=expected:continue
            elif (cwd/argument).resolve()!=expected and not (argument.name==name+'.py' and (cwd==target or target in cwd.parents)):continue
            cmd[index]=str(target/(name+'.py'))
            env={os.fsdecode(k):os.fsdecode(v) for k,v in (item.split(b'=',1) for item in (proc/'environ').read_bytes().split(b'\0') if b'=' in item)}
            candidates.append({'pid':signal_pid,'proc_id':proc.name,'command':cmd,'cwd':str(cwd),'env':env,'name':name,'was_running':True})
        except (OSError,ValueError):continue
    if len(candidates)>1:raise RuntimeError('Multiple '+name+' processes belong to this application; no process was stopped.')
    if not candidates and required:raise RuntimeError('No running '+name+' for this application; no files changed.')
    return candidates[0] if candidates else None

def service_port(service):
    command=service['command']
    for index,value in enumerate(command):
        if value=='--port' and index+1<len(command):return int(command[index+1])
        if value.startswith('--port='):return int(value.split('=',1)[1])
    key,default={'reference_server':('VQ_REFERENCE_INTERNAL_PORT',8182),'kb_server':('VQ_KB_INTERNAL_PORT',8181),'gateway':('VQ_PORT',8179)}[service['name']]
    return int(service['env'].get(key) or default)

def endpoint_ok(port,path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:%s%s'%(port,path),timeout=3) as response:
            return bool(json.loads(response.read().decode()).get('ok'))
    except Exception:return False

def plan_services(target):
    """Discover existing services before changing anything; bootstrap absent
    backends with the serving Python/environment instead of requiring all three.
    """
    target=Path(target).resolve()
    found={name:snapshot(target,name,required=False) for name in ('reference_server','kb_server','gateway')}
    anchor=found['reference_server'] or found['gateway'] or found['kb_server']
    if not anchor:raise RuntimeError('No teaching service belongs to this directory; no files changed. The live application may use another directory.')
    inherited=dict(anchor['env'])
    # Legacy releases embedded machine-specific executable paths in defaults.
    # Carry literal old defaults forward locally, without evaluating old code.
    for name in found:
        try:
            tree=ast.parse((target/(name+'.py')).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call) or len(node.args)<2:continue
                fn=node.func
                if not (isinstance(fn,ast.Attribute) and fn.attr=='get' and isinstance(fn.value,ast.Attribute) and fn.value.attr=='environ' and isinstance(fn.value.value,ast.Name) and fn.value.value.id=='os'):continue
                key,default=node.args[:2]
                if isinstance(key,ast.Str) and isinstance(default,ast.Str) and default.s.startswith('/') and not default.s.startswith('/path/to/'):
                    inherited.setdefault(key.s,default.s)
        except (OSError,SyntaxError,ValueError):pass
    if found['gateway']:
        for key in ('VQ_KB_INTERNAL_PORT','VQ_REFERENCE_INTERNAL_PORT','VQ_PORT'):
            if found['gateway']['env'].get(key):inherited[key]=found['gateway']['env'][key]
    services=[]
    for name,service in found.items():
        if service:
            for key,value in inherited.items():
                if key.startswith('VQ_'):service['env'].setdefault(key,value)
        else:
            env=dict(inherited);command=[anchor['command'][0],str(target/(name+'.py'))]
            if name!='gateway':command+=['--mode','ollama','--open','none']
            service={'pid':None,'command':command,'cwd':str(target),'env':env,'name':name,'was_running':False}
            port=service_port(service)
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                try:probe.bind(('127.0.0.1',port))
                except OSError:
                    # A shared KB endpoint can intentionally live in a separate
                    # release. Keep it untouched; gateway/auth must be managed.
                    if name=='kb_server' and endpoint_ok(port,'/api/health'):service['external']=True
                    else:raise RuntimeError('%s port %s is occupied by another service; no files or processes changed.'%(name,port))
            service['command']+=['--host','127.0.0.1' if name!='gateway' else env.get('VQ_HOST','0.0.0.0'),'--port',str(port)]
        services.append(service)
    gateway=next(row for row in services if row['name']=='gateway')
    gateway['env']['VQ_KB_INTERNAL_PORT']=str(service_port(next(row for row in services if row['name']=='kb_server')))
    gateway['env']['VQ_REFERENCE_INTERNAL_PORT']=str(service_port(next(row for row in services if row['name']=='reference_server')))
    return services

def stop(service):
    if service.get('external') or not service.get('pid'):return
    process=service.get('_process')
    if process is not None:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:raise RuntimeError(service['name']+' did not stop; no forced kill was sent.')
        service['pid']=None;service.pop('_process',None);return
    pid=service['pid']
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:service['pid']=None;return
    for _ in range(50):
        try:
            state=Path('/proc/%s/stat'%service.get('proc_id',pid)).read_text().rsplit(')',1)[1].strip()
            if state.startswith('Z'):break
        except FileNotFoundError:break
        time.sleep(.2)
    else:raise RuntimeError(service['name']+' did not stop; no forced kill was sent.')
    service['pid']=None

def restart(target,service):
    if service.get('external'):return
    stop(service)
    with (target/'logs'/(service['name']+'_sync.log')).open('ab') as log:
        process=subprocess.Popen(service['command'],cwd=service['cwd'],env=service['env'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    service['pid']=process.pid
    service['_process']=process;service.pop('proc_id',None)
    time.sleep(.1)
    if process.poll() is not None:raise RuntimeError(service['name']+' exited during startup; see logs/'+service['name']+'_sync.log')
    name={'reference_server':'reference_backend','kb_server':'kb_backend','gateway':'gateway'}[service['name']]
    (target/'logs'/(name+'.pid')).write_text(str(process.pid)+'\n')

def restore_services(target,services):
    errors=[]
    for service in services:
        try:
            if service.get('was_running',True):restart(target,service)
            else:stop(service)
        except Exception as error:errors.append(service['name']+': '+str(error))
    return errors

def install_files(source,target,backup,relative_paths):
    plan=[]
    for relative in relative_paths:
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Invalid source path')
        if relative in PRESERVE and (target/path).exists():continue
        if path.parts[0] in ('auth','logs','course_workspaces','backups') or path.suffix in ('.sqlite','.sqlite3','.db','.env'):raise ValueError('Persistent data cannot be installed from source')
        src=source/path;dest=target/path
        if dest.parent.resolve()!=target and target not in dest.parent.resolve().parents:raise ValueError('Target parent escapes application')
        if src.is_symlink() or dest.is_symlink():raise ValueError('Symlinked application file')
        existed=dest.exists();plan.append({'path':relative,'existed':existed})
        if existed:
            saved=backup/path;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(str(dest),str(saved))
    (backup/'plan.json').write_text(json.dumps(plan))
    try:
        for row in plan:
            path=Path(row['path']);dest=target/path;dest.parent.mkdir(parents=True,exist_ok=True)
            temp=dest.with_name(dest.name+'.sync_tmp')
            shutil.copy2(str(source/path),str(temp));os.replace(str(temp),str(dest))
    except Exception:
        restore_files(target,backup);raise
    return plan

def restore_files(target,backup):
    for row in json.loads((backup/'plan.json').read_text()):
        dest=target/row['path']
        if row['existed']:shutil.copy2(str(backup/row['path']),str(dest))
        elif dest.exists():dest.unlink()

def health(services=None):
    checks=[(service_port(row),'/api/reference/ready' if row['name']=='reference_server' else '/api/health' if row.get('external') else '/api/ready',not row.get('external'),row['name']) for row in services] if services else [(8182,'/api/reference/ready',True,'reference_server')]
    errors={}
    for _ in range(20):
        try:
            for port,path,require_revision,name in checks:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%s%s'%(port,path),timeout=3) as response:data=json.loads(response.read().decode())
                    if not data.get('ok') or (require_revision and (data.get('content_integrity_revision')!='R15' or data.get('release_revision')!='R16')):raise RuntimeError('Ready response has incorrect revision')
                    errors.pop(name,None)
                except Exception as error:errors[name]='%s %s: %s'%(port,path,str(error)[:180])
            if not errors:return
        except Exception as error:errors['health']=str(error)[:180]
        time.sleep(.5)
    raise RuntimeError('Readiness checks failed: '+'; '.join(name+' '+reason for name,reason in errors.items()))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);parser.add_argument('--apply',action='store_true');parser.add_argument('--publish',action='store_true')
    args=parser.parse_args();target=Path(args.target).resolve()
    if not (target/'reference_server.py').is_file():raise RuntimeError('Target is not the existing teaching application')
    (target/'logs').mkdir(exist_ok=True)
    with (target/'logs/auto_sync.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if run(['git','status','--porcelain','--untracked-files=no']):raise RuntimeError('Source checkout has local edits; no forced reset was performed')
        run(['git','fetch','origin','main']);sha=run(['git','rev-parse','origin/main'])
        state=target/'logs/auto_sync_state.json'
        previous=json.loads(state.read_text()) if state.exists() else {}
        if previous.get('commit')==sha and (not args.publish or previous.get('published')):
            # Also covers the first run after this updater learned to move the site: the
            # update itself was installed by the previous version of this script.
            if args.publish and not previous.get('site_url') and time.time()-float(previous.get('site_attempt_at') or 0)>SITE_RETRY_SECONDS:
                fields=site_step(target,load_config(target/'wrangler.jsonc',target),{})
                for key in ('site_pending','site_attempt_at'):previous.pop(key,None)
                previous.update(fields);state.write_text(json.dumps(previous))
            print('Already synchronized: '+sha);return
        if not ci_passed(sha):print('Waiting for successful Offline checks on '+sha);return
        if not args.apply:print('CI-approved update available: '+sha);return
        run(['git','merge','--ff-only','origin/main'])
        if run(['git','rev-parse','HEAD'])!=sha:raise RuntimeError('Source has local commits beyond CI-approved main; no files changed')
        services=plan_services(target)
        print('SERVICE PLAN: '+', '.join(row['name']+(' keep existing endpoint' if row.get('external') else ' restart' if row['was_running'] else ' start missing service') for row in services),flush=True)
        python=services[0]['command'][0]
        subprocess.check_call([python,'-c','import fitz,course_runtime,kb_server,reference_server,gateway,json; from model_lock import SharedModelLock; from pdf_ocr import LocalOCR; from pdf_vision import LocalVision; assert course_runtime.fitz is not None; assert kb_server.SharedModelLock is reference_server.SharedModelLock is SharedModelLock; print("LOCAL OCR: "+json.dumps(LocalOCR().report())); print("LOCAL VISION: "+json.dumps(LocalVision().report()))'],cwd=str(ROOT/'app'),env=services[0]['env'])
        if args.publish and not shutil.which('npx'):raise RuntimeError('Node.js/npx is unavailable; no application files changed')
        paths=[path[4:] for path in run(['git','ls-files','app']).splitlines() if path.startswith('app/')]
        cfg=load_config(target/'wrangler.jsonc',target) if args.publish else None
        backup=target/'backups'/('auto_sync_'+time.strftime('%Y%m%d_%H%M%S'));backup.mkdir(parents=True)
        install_files(ROOT/'app',target,backup,paths)
        try:
            for service in services:restart(target,service)
            health(services)
            if args.publish:
                # Preserve the existing Worker name, routing and bindings.
                # Omitting local vars prevents replacing its live origin.
                with tempfile.NamedTemporaryFile(mode='w',suffix='.json',dir=str(target),delete=False) as file:json.dump(cfg,file);config_path=file.name
                try:subprocess.check_call(['npx','--yes','wrangler@4.127.0','deploy','--config',config_path,'--keep-vars'],cwd=str(target))
                finally:os.unlink(config_path)
            record={'commit':sha,'updated_at':time.time(),'published':args.publish,'backup':str(backup)}
            if args.publish:record.update(site_step(target,cfg,previous))
            state.write_text(json.dumps(record))
            print('SYNC COMPLETE '+sha+'; backup '+str(backup))
        except Exception:
            restore_files(target,backup)
            errors=restore_services(target,services)
            if errors:print('ROLLBACK REQUIRES ATTENTION: '+'; '.join(errors),flush=True)
            raise

if __name__=='__main__':
    try:main()
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
        print('UPDATE STOPPED: '+str(error),flush=True);raise SystemExit(1)
