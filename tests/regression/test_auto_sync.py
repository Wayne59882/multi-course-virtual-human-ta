"""Verify source installation/rollback without contacting a teaching server."""
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('auto_sync',ROOT/'scripts/deploy/auto_sync.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);source=root/'source';target=root/'target';backup=root/'backup'
    for path in (source,target,backup):path.mkdir()
    (source/'reference_server.py').write_text('new code')
    (source/'content_integrity.py').write_text('new module')
    (source/'wrangler.jsonc').write_text('{"name":"different-worker"}')
    (target/'reference_server.py').write_text('old code')
    (target/'wrangler.jsonc').write_text('{// existing settings\n"name":"lungta","compatibility_date":"2026-08-28","assets":{"directory":"web","binding":"ASSETS"},"vars":{"ORIGIN_URL":"https://current.example"},}')
    (target/'course_workspaces').mkdir();(target/'course_workspaces/original.pdf').write_bytes(b'private PDF')
    (target/'auth').mkdir();(target/'auth/auth.sqlite3').write_bytes(b'private accounts')
    sync.install_files(source,target,backup,['reference_server.py','content_integrity.py','wrangler.jsonc'])
    assert (target/'reference_server.py').read_text()=='new code'
    config=sync.load_config(target/'wrangler.jsonc',target)
    assert config['name']=='lungta' and config['keep_vars'] and 'vars' not in config
    assert config['compatibility_date']=='2026-08-28' and config['assets']['binding']=='ASSETS'
    sync.restore_files(target,backup)
    assert (target/'reference_server.py').read_text()=='old code'
    assert not (target/'content_integrity.py').exists()
    assert (target/'course_workspaces/original.pdf').read_bytes()==b'private PDF'
    assert (target/'auth/auth.sqlite3').read_bytes()==b'private accounts'
    try:sync.install_files(source,target,backup,['../outside.py'])
    except ValueError:pass
    else:raise AssertionError('unsafe path accepted')
    # Site address: a proxy Worker with the thesis name is deployed and checked first;
    # only then does the existing Worker learn SITE_URL and forward its old address.
    (target/'logs').mkdir()
    deployed=[]
    def fake_deploy(where,cfg,extra):
        deployed.append((cfg['name'],cfg.get('services'),list(extra)))
        return 'Deployed %s\n  https://%s.twchu.workers.dev\n'%(cfg['name'],cfg['name'])
    answers=iter([False,True]);waits=[]
    site=sync.move_site(target,config,deploy=fake_deploy,check=lambda url:next(answers),wait=waits.append)
    assert site=='https://multicourse-ta.twchu.workers.dev' and waits==[5]
    assert deployed[0]==('multicourse-ta',[{'binding':'APP','service':'lungta'}],[])
    assert deployed[1]==('lungta',None,['--keep-vars','--var','SITE_URL:https://multicourse-ta.twchu.workers.dev'])
    assert (target/'logs/site_url.txt').read_text().strip()==site
    # A new address that never serves the site leaves the old address untouched.
    deployed.clear()
    try:sync.move_site(target,config,deploy=fake_deploy,check=lambda url:False,wait=lambda s:None);raise AssertionError('must not forward')
    except RuntimeError as error:assert 'does not serve' in str(error)
    assert [d[0] for d in deployed]==['multicourse-ta'],'SITE_URL is not set when the check fails'
    # When this server cannot reach workers.dev, the wrangler-validated binding is trusted.
    deployed.clear();assert sync.move_site(target,config,deploy=fake_deploy,check=lambda url:None)==site and len(deployed)==2
    # Already moved, or already the thesis name: nothing is deployed again; failures never raise.
    assert sync.site_step(target,config,{'site_url':site})=={'site_url':site}
    assert sync.move_site(target,dict(config,name='multicourse-ta'),deploy=lambda *a:1/0) is None
    original=sync.move_site
    sync.move_site=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('wrangler offline'))
    try:fields=sync.site_step(target,config,{})
    finally:sync.move_site=original
    assert fields['site_pending'] is True and fields['site_attempt_at']>0
# An update (which restarts the server) waits while a course build is reading pages, at most 3 h.
import time
with tempfile.TemporaryDirectory() as temporary:
    target=Path(temporary);jobs=target/'course_workspaces'/'_jobs';jobs.mkdir(parents=True)
    now=time.time()
    (jobs/'old.json').write_text(json.dumps({'id':'old','status':'running','course_name':'Stale','updated_at':now-3600}))
    (jobs/'done.json').write_text(json.dumps({'id':'done','status':'done','course_name':'Finished','updated_at':now}))
    assert sync.running_build(target,now)==''  # a job not updated for an hour is not a running build
    (jobs/'live.json').write_text(json.dumps({'id':'live','status':'running','course_name':'Genomic study','updated_at':now-30}))
    assert sync.running_build(target,now)=='Genomic study'
    previous={}
    assert sync.defer_update(previous,'Genomic study',now) and previous['deferred_since']==now
    assert sync.defer_update(previous,'Genomic study',now+3600)
    assert not sync.defer_update(previous,'Genomic study',now+sync.MAX_BUILD_DEFER+1)
    assert not sync.defer_update({},'',now)
print('PASS atomic source install/rollback, private PDF/account preservation, original Worker name/bindings and remote variables retained, '
      'site address moved only after the new address serves the site; updates wait for a running course build (at most 3 h)')
