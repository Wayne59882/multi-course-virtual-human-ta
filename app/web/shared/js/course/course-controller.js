/* R10 course name from PDF content */
(function(){
'use strict';
const ACTIVE_KEY='VQ_V241_ACTIVE_COURSE_ID';
const READY_NOTICE='VQ_V241_COURSE_READY';
const PENDING_BASE='VQ_V241_PENDING_COURSE_STAGE';
const OPEN_NEWEST='VQ_OPEN_NEWEST_COURSE';
let PENDING_KEY=PENDING_BASE,DRAFT_KEY='current',builds=[];
const $=id=>document.getElementById(id);
let activity=null,manifest=null,inspection=null,legacyAnswer=null,legacyCaseHTML='',quizState=null,adapterInstalled=false,draft=null,uploading=false,polling=false;

function dbOpen(){return new Promise((resolve,reject)=>{if(!window.indexedDB){reject(new Error('This browser does not support resumable file storage.'));return;}const r=indexedDB.open('VQCourseUpload',1);r.onupgradeneeded=()=>r.result.createObjectStore('drafts');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
async function draftIO(mode,value){const db=await dbOpen();return new Promise((resolve,reject)=>{const tx=db.transaction('drafts',mode),r=mode==='readonly'?tx.objectStore('drafts').get(DRAFT_KEY):tx.objectStore('drafts').put(value,DRAFT_KEY);let result;r.onsuccess=()=>{result=r.result;};r.onerror=()=>reject(r.error);tx.oncomplete=()=>{db.close();resolve(result);};tx.onerror=()=>{db.close();reject(tx.error);};});}
async function saveDraft(){if(draft)await draftIO('readwrite',draft);}
async function useAccountKeys(){
  // Uploads are kept per account, so switching between the administrator and a teacher never loses one.
  let id='';try{const r=await fetch('/api/auth/me',{cache:'no-store'}),d=await r.json();id=d&&d.user?String(d.user.id):'';}catch(_){}
  if(id){DRAFT_KEY='current:'+id;PENDING_KEY=PENDING_BASE+':'+id;}
}
function newId(){return window.crypto&&crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);}
function pendingStage(){try{return localStorage.getItem(PENDING_KEY)||'';}catch(_){return '';}}
function setPending(v){try{if(v)localStorage.setItem(PENDING_KEY,v);else localStorage.removeItem(PENDING_KEY);}catch(_){}}

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function modelKey(){const s=$('localModelSelect');return (s&&s.value)||'gemma';}
function cloudCfg(){try{return (window.VQ_getCloudConfig&&window.VQ_getCloudConfig())||{provider:'off',apiKey:''};}catch(_){return {provider:'off',apiKey:''};}}
function qLang(q){try{return String((window.VQ_resolveInputLanguage&&window.VQ_resolveInputLanguage(String(q||''),null))||(window.VQ_detectLanguage&&window.VQ_detectLanguage(String(q||'')))||'en-US');}catch(_){return 'en-US';}}
function setBuildStatus(text,kind){const el=$('v241BuildStatus');if(!el)return;el.textContent=String(text||'');el.className='v241-status'+(kind?' v241-'+kind:'');}
function setMeter(name,value,label){const wrap=$('v241WorkProgress'),row=$('v241Meter'+name),bar=$('v241Bar'+name),detail=$('v241Label'+name);if(!wrap||!row)return;wrap.hidden=false;row.hidden=false;bar.value=Math.max(0,Math.min(100,Number(value)||0));detail.textContent=String(label||Math.round(bar.value)+'%');}
function resetMeters(){const wrap=$('v241WorkProgress');if(wrap)wrap.hidden=true;for(const name of ['Upload','Activate']){const row=$('v241Meter'+name);if(row)row.hidden=true;}}
function activeId(){try{return localStorage.getItem(ACTIVE_KEY)||'';}catch(_){return '';}}
function setActiveId(v){try{if(v)localStorage.setItem(ACTIVE_KEY,v);else localStorage.removeItem(ACTIVE_KEY);}catch(_){}applyVisualModule();}

function injectBuilder(){
  const app=document.querySelector('.app'),title=app&&app.querySelector('.title');if(!app||!title||$('v241CourseBuilder'))return;
  title.id=title.id||'appTitle';
  const wrap=document.createElement('section');wrap.id='v241CourseBuilder';wrap.className='v241-course-builder';
  wrap.innerHTML=`<div class="v241-head"><div><h2>Build a Course</h2><div class="v241-sub">Upload course PDFs to detect chapters, prepare lecture scripts, build course Q&amp;A, and prepare practice questions.</div></div><span class="v241-course-badge" id="v241ActiveBadge">Built-in Physiology</span></div>
  <div class="v241-grid">
    <div class="v241-field"><label for="v241CourseName">Course Name (optional)</label><input id="v241CourseName" type="text" name="vq-course-title" autocomplete="off" data-lpignore="true" data-1p-ignore placeholder="Leave blank to detect from the PDF content"></div>
    <div class="v241-field v241-pdf-field"><label for="v241PdfFiles">Course Materials (PDF)</label><input id="v241PdfFiles" type="file" accept="application/pdf,.pdf" multiple></div>
  </div>
  <div class="v241-actions"><button type="button" class="v241-primary" id="v241InspectBtn">Upload &amp; Inspect Course Materials</button><button type="button" id="v241BuildBtn" disabled>Build Teaching Assistant</button><button type="button" id="v241RetryPrep" hidden>Retry Background Preparation</button><button type="button" id="v241CancelBuild" hidden>Cancel Build</button></div>
  <div class="v241-status" id="v241BuildStatus" role="status" aria-live="polite">Select searchable PDFs, inspect the teaching units, then build. Course Q&amp;A opens after extraction; reviewed exam-style questions become playable as soon as two per unit are ready.</div>
  <div class="v241-meters" id="v241WorkProgress" hidden><div id="v241MeterUpload" hidden><span>PDF upload</span><span id="v241LabelUpload"></span><progress id="v241BarUpload" max="100" value="0"></progress></div><div id="v241MeterActivate" hidden><span>Course activation</span><span id="v241LabelActivate"></span><progress id="v241BarActivate" max="100" value="0"></progress></div></div>
  <div class="v241-unit-wrap" id="v241UnitWrap" hidden><div class="v241-unit-title">Teaching units found — check each name and page range before building</div><div class="v241-unit-hint">✎ Click a unit name or page number to correct it. Untick units you do not need.</div><div class="v241-unit-toolbar"><div class="v241-unit-summary" id="v241UnitSummary"></div><div class="v241-unit-buttons"><button type="button" id="v241SelectAll">Select all</button><button type="button" id="v241ClearAll">Clear all</button></div></div><div class="v241-unit-list" id="v241UnitList"></div></div>
  <div class="v241-builds" id="v241Builds" hidden></div>
  <div class="v241-active"><div class="v241-field"><label for="v241ExistingCourses">Currently teaching</label><select class="v241-active-select" id="v241ExistingCourses"><option value="">Built-in Physiology (default)</option></select></div><div class="v241-actions" style="margin:0"><button type="button" id="v241DeleteCourse" disabled>Delete selected course</button></div></div><div class="v241-prep" id="v241Prep" hidden></div><div class="v241-sub">Select a saved course to teach or delete it when it is no longer needed. The built-in course cannot be deleted.</div>`;
  title.insertAdjacentElement('afterend',wrap);
  $('v241InspectBtn').addEventListener('click',inspectCourse);
  $('v241BuildBtn').addEventListener('click',buildCourse);
  $('v241CancelBuild').addEventListener('click',cancelBuild);
  $('v241DeleteCourse').addEventListener('click',deleteSelectedCourse);
  $('v241RetryPrep').addEventListener('click',retryPreparation);
  $('v241SelectAll').addEventListener('click',()=>selectAllUnits(true));
  $('v241ClearAll').addEventListener('click',()=>selectAllUnits(false));
  $('v241ExistingCourses').addEventListener('change',e=>{const cid=String(e.target.value||'');if(window.VQ_beforeCourseSwitch&&window.VQ_beforeCourseSwitch(cid))return;setActiveId(cid);location.reload();});
}

async function inspectCourse(){
  const files=[...(($('v241PdfFiles')||{}).files||[])];
  if(draft&&draft.phase==='uploading'&&draft.files&&draft.files.length&&(!files.length||files.length===draft.files.length&&files.every((f,i)=>f.name===draft.files[i].file.name&&f.size===draft.files[i].file.size))){try{await resumeUpload();}catch(e){setBuildStatus('Upload paused: '+String(e.message||e)+'. Try again after checking the connection.','error');}return;}
  if(!files.length){setBuildStatus('Select at least one PDF before inspection.','error');return;}
  const btn=$('v241InspectBtn');btn.disabled=true;inspection=null;$('v241BuildBtn').disabled=true;$('v241UnitWrap').hidden=true;
  try{
    resetMeters();draft={stageId:newId(),courseName:String(($('v241CourseName')||{}).value||''),files:files.map(f=>({file:f,id:newId()})),nextIndex:0,phase:'uploading',selections:{}};
    await saveDraft();setPending(draft.stageId);await resumeUpload();
  }catch(e){setBuildStatus('Upload paused: '+String(e&&e.message||e)+'. Refresh to resume, or select the PDFs again.','error');btn.disabled=false;}
}
const RESTARTING=[0,502,503,504,520,521,522,523,524,525,526,527,530];
async function getJSON(url,doing){
  // An update restarts the server for a minute or two: keep retrying instead of stopping.
  const started=Date.now();let wait=2000;
  for(;;){
    let r=null,d=null;
    try{r=await fetch(url,{cache:'no-store'});d=await r.json();}catch(_){d=null;}
    if(r&&d&&!RESTARTING.includes(r.status))return {r,d};
    if(Date.now()-started>15*60*1000)throw new Error('The server did not answer for 15 minutes. Refresh to retry; nothing is lost.');
    if(doing)setBuildStatus(doing+' — the server is restarting (an update); continuing automatically…');
    await new Promise(res=>setTimeout(res,wait));wait=Math.min(15000,wait*1.6);
  }
}
async function waitForInspection(base,i,count){
  // Reading the outline of a long PDF can take minutes; the server does it in the background.
  const started=Date.now();
  for(;;){
    const seconds=Math.round((Date.now()-started)/1000);
    setMeter('Upload',100,'Inspecting teaching units… '+seconds+' s');setBuildStatus('PDF '+(i+1)+' / '+count+' received. Finding the teaching units… ('+seconds+' s). You may refresh; this continues on the server.');
    const {r,d}=await getJSON(base,'Finding the teaching units');
    if(!r.ok||!d.ok)throw new Error(d.error||('HTTP '+r.status));
    if(d.complete)return d.inspection||null;
    if(Date.now()-started>30*60*1000)throw new Error('Inspection is taking more than 30 minutes. Refresh to keep waiting.');
    await new Promise(res=>setTimeout(res,3000));
  }
}
async function resumeUpload(){
  if(!draft||uploading||draft.phase!=='uploading')return;uploading=true;$('v241InspectBtn').disabled=true;
  try{
    const allBytes=draft.files.reduce((sum,x)=>sum+x.file.size,0);let previousBytes=draft.files.slice(0,draft.nextIndex).reduce((sum,x)=>sum+x.file.size,0);
    for(let i=draft.nextIndex;i<draft.files.length;i++){
      const item=draft.files[i],file=item.file,base='/api/reference/course-upload-status?stage_id='+encodeURIComponent(draft.stageId)+'&upload_id='+encodeURIComponent(item.id);
      const {r:s,d:state}=await getJSON(base,'Uploading');if(!s.ok||!state.ok)throw new Error(state.error||'Upload status unavailable');let offset=Number(state.offset||0),failures=0;
      setMeter('Upload',100*(previousBytes+offset)/allBytes,Math.round(100*(previousBytes+offset)/allBytes)+'% · '+(i+1)+' / '+draft.files.length+' PDFs');
      if(!state.complete&&offset===file.size)state.inspection=await waitForInspection(base,i,draft.files.length);
      if(state.complete||(offset===file.size&&state.inspection)){inspection=state.inspection;draft.nextIndex=i+1;previousBytes+=file.size;await saveDraft();continue;}
      while(offset<file.size){
        const end=Math.min(offset+8*1024*1024,file.size),fd=new FormData();fd.append('stage_id',draft.stageId);fd.append('upload_id',item.id);fd.append('file_name',file.name);fd.append('total_size',String(file.size));fd.append('offset',String(offset));fd.append('course_name',draft.courseName);fd.append('chunk',file.slice(offset,end),file.name);
        setBuildStatus(end===file.size?'PDF '+(i+1)+' / '+draft.files.length+' uploaded; inspecting teaching units…':'Uploading PDF '+(i+1)+' / '+draft.files.length+': '+file.name+' · '+Math.round(100*offset/file.size)+'%. You may refresh; upload resumes from the saved chunk.');
        let response=null,data=null;try{response=await fetch('/api/reference/course-upload-chunk',{method:'POST',body:fd,cache:'no-store'});data=await response.json();}catch(_){data=null;}
        if(!data||!response||RESTARTING.includes(response.status)){
          // No answer (for example during an update): ask where the server is, then send from there.
          const {d:check}=await getJSON(base,'Uploading');if(!check.ok)throw new Error(check.error||'Upload status unavailable');
          if(Number(check.offset)>offset||check.complete){data=check;response=null;}
          else{if(++failures>30)throw new Error('The upload keeps failing. Refresh to retry; the received part is kept.');continue;}
        }
        failures=0;
        if(!data.ok||(response&&!response.ok))throw new Error(data.detail||data.error||'Upload HTTP '+(response&&response.status));
        offset=Number(data.offset);if(data.inspection)inspection=data.inspection;
        if(offset===file.size&&!data.complete)inspection=await waitForInspection(base,i,draft.files.length)||inspection;
        setMeter('Upload',100*(previousBytes+offset)/allBytes,Math.round(100*(previousBytes+offset)/allBytes)+'% · '+(i+1)+' / '+draft.files.length+' PDFs');
      }
      draft.nextIndex=i+1;previousBytes+=file.size;await saveDraft();
    }
    if(!inspection){const r=await fetch('/api/reference/course-stage-status?stage_id='+encodeURIComponent(draft.stageId),{cache:'no-store'}),d=await r.json();if(!r.ok||!d.inspection)throw new Error('The server has not finished inspecting the staged PDFs. Refresh to retry.');inspection=d.inspection;}
    draft.phase='inspected';draft.inspection=inspection;draft.files=[];await saveDraft();
    renderInspection();
    setMeter('Upload',100,'Upload and chapter inspection complete');setBuildStatus('Upload complete. Review the teaching units, then build. Your upload and selections are saved across refreshes.','ok');$('v241BuildBtn').disabled=false;setPending('');
  }finally{uploading=false;$('v241InspectBtn').disabled=false;}
}
function renderInspection(){
  const list=$('v241UnitList'),wrap=$('v241UnitWrap'),summary=$('v241UnitSummary');if(!list||!inspection)return;wrap.hidden=false;list.innerHTML='';
  const units=inspection.units||[];
  units.forEach((u,i)=>{const row=document.createElement('div');row.className='v241-unit-row';row.dataset.unitId=u.id;const saved=draft&&draft.selections&&draft.selections[u.id],pages=sourcePages(u),start=saved&&saved.start_page||u.start_page,end=saved&&saved.end_page||u.end_page;
    row.innerHTML=`<input class="v241-unit-check" type="checkbox" ${!saved||saved.enabled?'checked':''} aria-label="Include unit ${i+1}"><input class="v241-unit-name" type="text" value="${esc(saved&&saved.title||u.title)}" aria-label="Unit ${i+1} name" title="Click to rename this unit"><span class="v241-unit-meta"><span class="v241-unit-source" title="${esc(u.source_title)}">${esc(u.source_title)}</span> · pp. <input class="v241-unit-page v241-unit-start" type="number" inputmode="numeric" min="1" max="${pages}" value="${start}" aria-label="Unit ${i+1} first page">–<input class="v241-unit-page v241-unit-end" type="number" inputmode="numeric" min="1" max="${pages}" value="${end}" aria-label="Unit ${i+1} last page">${u.needs_review?' · review boundaries':''}</span>`;list.appendChild(row);});
  list.onchange=saveSelections;list.oninput=e=>{if(e.target.classList.contains('v241-unit-name')||e.target.classList.contains('v241-unit-page'))saveSelections();};updateSelectionSummary();
}
function sourcePages(u){const src=(inspection&&inspection.sources||[]).find(x=>x.file_name===u.source_file);return Number(src&&src.pages)||Number(u.end_page)||1;}
function rowPages(row){return {start:parseInt(row.querySelector('.v241-unit-start').value,10),end:parseInt(row.querySelector('.v241-unit-end').value,10)};}
function updateSelectionSummary(){const el=$('v241UnitSummary');if(!el||!inspection)return;const total=(inspection.units||[]).length,selected=selectedUnits().length;el.textContent=selected+' / '+total+' units selected';}
function saveSelections(){updateSelectionSummary();if(!draft)return;draft.selections={};for(const row of document.querySelectorAll('#v241UnitList .v241-unit-row')){const p=rowPages(row);draft.selections[row.dataset.unitId]={enabled:row.querySelector('.v241-unit-check').checked,title:row.querySelector('.v241-unit-name').value,start_page:p.start||undefined,end_page:p.end||undefined};}saveDraft().catch(e=>setBuildStatus('Could not save unit selection: '+String(e.message||e),'error'));}
function selectAllUnits(checked){for(const box of document.querySelectorAll('#v241UnitList .v241-unit-check'))box.checked=checked;saveSelections();}
function selectedUnits(){return [...document.querySelectorAll('#v241UnitList .v241-unit-row')].filter(r=>r.querySelector('.v241-unit-check').checked).map(r=>{const p=rowPages(r);return {id:r.dataset.unitId,title:String(r.querySelector('.v241-unit-name').value||'').trim(),start_page:p.start,end_page:p.end};});}
function unitProblem(units){for(const u of units){const unit=(inspection.units||[]).find(x=>x.id===u.id),pages=unit?sourcePages(unit):0;if(!u.title)return 'Every selected unit needs a name.';if(!Number.isInteger(u.start_page)||!Number.isInteger(u.end_page)||u.start_page<1||u.end_page>pages||u.start_page>u.end_page)return 'Check the pages of “'+u.title+'”: they must be between 1 and '+pages+', first page not after last page.';}return '';}
async function buildCourse(){
  if(!inspection){setBuildStatus('Inspect the uploaded PDFs first.','error');return;}const units=selectedUnits();if(!units.length){setBuildStatus('Select at least one chapter/unit.','error');return;}const problem=unitProblem(units);if(problem){setBuildStatus(problem,'error');return;}
  const body={stage_id:inspection.stage_id,course_name:String($('v241CourseName').value||'').trim(),units,model_key:modelKey(),reading:'fast'};
  $('v241BuildBtn').disabled=true;setMeter('Activate',0,'Extracting course material');setBuildStatus('Extracting selected units for course Q&A. You may refresh; the server continues the build.');
  try{
    if(draft){draft.phase='building';draft.buildBody=body;await saveDraft();setPending(draft.stageId);}
    let r=await fetch('/api/reference/course-build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'}),d=await r.json().catch(()=>({}));
    if(!r.ok||!d.ok)throw new Error(d.detail||d.error||('Build request HTTP '+r.status));
    await pollBuild(d.job.id);
  }catch(e){setBuildStatus('Could not reconnect to the course build: '+String(e&&e.message||e)+'. Refresh to reconnect.','error');$('v241BuildBtn').disabled=false;}
}
let buildJobId='';
function showCancel(jid){buildJobId=jid||'';const b=$('v241CancelBuild');if(b){b.hidden=!jid;b.disabled=false;}}
async function buildCancelled(){
  setPending('');if(draft){draft.phase='inspected';delete draft.buildBody;await saveDraft();}
  showCancel('');$('v241BuildBtn').disabled=!inspection;
  setBuildStatus('Course build cancelled. Nothing was added to your courses. You can build again or upload different PDFs.','ok');
}
async function cancelBuild(){
  const jid=buildJobId;if(!jid)return;
  if(!window.confirm('Stop building this course now?\nThe course will not be created. You can build it again later.'))return;
  const b=$('v241CancelBuild');if(b)b.disabled=true;setBuildStatus('Cancelling the course build…');
  try{const r=await fetch('/api/reference/course-build-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:jid}),cache:'no-store'}),d=await r.json().catch(()=>({}));
    if(!r.ok||!d.ok)throw new Error(d.detail||d.error||('HTTP '+r.status));
    if(d.deleted){if(activeId()===d.course_id)setActiveId('');await buildCancelled();await loadCourseList();return;}
    await buildCancelled();}
  catch(e){setBuildStatus('Could not cancel the build: '+String(e&&e.message||e),'error');if(b)b.disabled=false;}
}
async function pollBuild(jid){
  if(polling)return;polling=true;showCancel(jid);
  try{
  for(;;){
    if(buildJobId!==jid)return;
    const {r,d}=await getJSON('/api/reference/course-job-status?id='+encodeURIComponent(jid),'Building');if(!r.ok)throw new Error(d.error||('HTTP '+r.status));
    setBuildStatus('Building course: '+String(d.progress||0)+'%\n'+String(d.message||'Working…'));
    setMeter('Activate',d.progress||0,String(d.message||'Extracting material'));
    if(d.status==='done'){
      const cid=d.result&&d.result.course_id;if(!cid)throw new Error('Build completed without a course ID.');
      showCancel('');setActiveId(cid);setPending('');if(draft){draft.phase='active';draft.files=[];await saveDraft();}try{sessionStorage.setItem(READY_NOTICE,'Course Q&A is active. Exam-style questions can be drawn as soon as two per unit pass review.');}catch(_){}
      location.reload();return;
    }
    if(d.status==='cancelled'){await buildCancelled();return;}
    if(d.status==='error'){
      showCancel('');
      setBuildStatus('Course build stopped at '+String(d.progress||0)+'% ('+String(d.phase||'processing')+').\n'+String(d.error||d.message||'Unknown backend error')+'\nThe uploaded PDFs remain selected; you can retry after correcting this cause.','error');
      $('v241BuildBtn').disabled=false;return;
    }
    await new Promise(res=>setTimeout(res,2000));
  }
  }finally{polling=false;if(buildJobId===jid)showCancel('');}
}

async function loadCourseList(){
  const sel=$('v241ExistingCourses');if(!sel)return;
  try{const r=await fetch('/api/reference/course-list',{cache:'no-store'}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||('HTTP '+r.status));const current=activeId();sel.innerHTML='<option value="">Built-in Physiology (default)</option>'+(d.courses||[]).map(c=>`<option value="${esc(c.course_id)}">${esc(c.course_name)} · ${Number(c.unit_count||0)} units${c.owner_name&&!c.owned?' · '+esc(c.owner_name):''} · ${esc(c.course_id.slice(-8))}</option>`).join('');if(current&&![...sel.options].some(x=>x.value===current)){setActiveId('');setBuildStatus('The previous course is unavailable. Built-in Physiology is active.','error');}let newest=false;try{newest=!!sessionStorage.getItem(OPEN_NEWEST);sessionStorage.removeItem(OPEN_NEWEST);}catch(_){}
    if(newest&&!activeId()){const latest=(d.courses||[]).slice().sort((a,b)=>Number(b.created_at||0)-Number(a.created_at||0))[0];if(latest)setActiveId(latest.course_id);}
    sel.value=activeId();const del=$('v241DeleteCourse');if(del)del.disabled=!sel.value;document.dispatchEvent(new Event('vq-courses-loaded'));}catch(e){setBuildStatus('Could not update the course list: '+String(e&&e.message||e),'error');}
}
async function deleteSelectedCourse(){
  const sel=$('v241ExistingCourses'),cid=String(sel&&sel.value||'');if(!cid)return;
  const label=sel.options[sel.selectedIndex].text;
  if(!window.confirm('Permanently delete this uploaded course and its PDFs, question bank and quiz statistics?\n'+label))return;
  const btn=$('v241DeleteCourse');btn.disabled=true;
  try{const r=await fetch('/api/reference/course-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:cid}),cache:'no-store'}),d=await r.json().catch(()=>({}));if(!r.ok||!d.ok)throw new Error(d.detail||d.error||('HTTP '+r.status));if(activeId()===cid){setActiveId('');location.reload();return;}await loadCourseList();setBuildStatus('Deleted '+label+'.'+(d.stopped_background&&d.stopped_background.length?' Its background preparation was stopped.':''),'ok');}
  catch(e){setBuildStatus('Course deletion failed: '+String(e&&e.message||e),'error');btn.disabled=false;}
}
// Builds in progress for this account (from any browser); the administrator sees every teacher's.
async function loadBuilds(){
  const box=$('v241Builds');if(!box)return;
  let list=[];try{const r=await fetch('/api/reference/course-builds',{cache:'no-store'});if(r.status===403){box.hidden=true;return 'stop';}const d=await r.json();if(!r.ok||!d.ok)return;list=d.builds||[];}catch(_){return;}
  const before=new Set(builds.filter(b=>b.status!=='error').map(b=>b.id));builds=list;
  const shown=list.filter(b=>b.id!==buildJobId);
  box.innerHTML=shown.map(b=>{const failed=b.status==='error',waiting=b.status==='queued',state=failed?'error':waiting?'waiting':'working';
    const label=failed?'Build stopped':waiting?'Waiting for the build before it':'Building '+Math.round(Number(b.progress||0))+'%';
    const detail=failed?(b.error||b.message||'Unknown error'):waiting?'Builds run one at a time; this starts when the build above finishes.':(b.message||'Working…');
    const title=(b.course_name||'').trim()||(b.files||[]).join(', ')||'Uploaded course';
    const files=(b.course_name&&(b.files||[]).length)?' · '+(b.files||[]).join(', '):'';
    return `<div class="v241-prep v241-build"><div class="v241-prep-head"><b>${esc(title)}${b.owner_name&&!b.owned?' · '+esc(b.owner_name):''}<small>${esc(files)}</small></b><span class="v241-prep-${state}">${esc(label)}</span></div><div class="v241-prep-row v241-prep-${state}"><span class="v241-prep-icon">${failed?'!':waiting?'…':'●'}</span><span class="v241-prep-label">${esc(detail)}</span><span class="v241-prep-detail">${failed?'':`<button type="button" class="v241-build-cancel" data-job="${esc(b.id)}">Cancel</button>`}</span><progress max="100" value="${Math.round(Number(b.progress||0))}"></progress></div></div>`;}).join('');
  box.hidden=!shown.length;
  box.querySelectorAll('.v241-build-cancel').forEach(btn=>btn.addEventListener('click',async()=>{
    if(!window.confirm('Cancel this course build? The uploaded PDF stays saved, and pages already read are kept for the next upload of the same PDF.'))return;
    btn.disabled=true;try{const r=await fetch('/api/reference/course-build-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:btn.dataset.job}),cache:'no-store'}),d=await r.json().catch(()=>({}));if(!r.ok||d.ok===false)throw new Error(d.error||('HTTP '+r.status));await loadBuilds();}
    catch(e){btn.disabled=false;setBuildStatus('Could not cancel the build: '+String(e&&e.message||e),'error');}}));
  if([...before].some(id=>!list.some(b=>b.id===id&&b.status!=='error'))){await loadCourseList();if(!activeId())setBuildStatus('A course finished building. Select it in Currently teaching.','ok');}
}
async function loadManifest(cid){
  if(!cid){manifest=null;window.VQ_UPLOADED_COURSE_ACTIVE=false;applyVisualModule();return;}
  try{/* R11: servers that prepare one course at a time resume the selected course; older servers fall back to the status read. */let r=await fetch('/api/reference/course-activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:cid,model_key:modelKey()}),cache:'no-store'}).catch(()=>null);if(!r||[404,405,501].includes(r.status))r=await fetch('/api/reference/course-status?course_id='+encodeURIComponent(cid),{cache:'no-store'});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'course unavailable');manifest=d.manifest;activity=d.activity||null;applyManifest();}catch(e){setActiveId('');manifest=null;applyVisualModule();setBuildStatus('Could not load selected course: '+String(e&&e.message||e)+'. Built-in course is active.','error');}
}
function applyManifest(){
  if(!manifest)return;window.VQ_UPLOADED_COURSE_ACTIVE=true;setActiveId(manifest.course_id);const title=$('appTitle')||document.querySelector('.app .title');if(title)title.textContent=manifest.course_name+' Virtual Human TA';const badge=$('v241ActiveBadge');if(badge)badge.textContent=manifest.course_name;
  // The existing lecture/voice controller owns the capture-phase click event.
  // Feed it the selected uploaded unit so highlighting, pause and resume all
  // use the same script as the current course.
  window.VQ_getActiveCourseLecture=id=>(manifest.units||[]).find(u=>u.id===id)||null;
  const sel=$('v241ExistingCourses');if(sel)sel.value=manifest.course_id;
  const lesson=$('lessonSelect');if(lesson){lesson.innerHTML=(manifest.units||[]).map((u,i)=>`<option value="${esc(u.id)}">Unit ${i+1}: ${esc(u.title)} · pp. ${u.start_page||'?'}–${u.end_page||'?'}</option>`).join('');lesson.onchange=()=>{showSelectedLectureIntro();const u=currentUnit();if(u&&u.script_status!=='ready')prioritizeUnit('lectures',u.id);};showSelectedLectureIntro();}
  applyVisualModule();installDynamicCase();installAnswerAdapter();
  showPreparation();
}
function quizUnits(){if(!manifest)return [];const scope=String(($('v241QuizUnit')||{}).value||'ready'),per=quizCount();return (manifest.units||[]).filter(u=>Number(u.question_count||0)>=per&&(scope==='ready'||u.id===scope));}
function quizReady(){return !!manifest&&manifest.question_generation==='exam-mcq-v5'&&quizUnits().length>0;}
function showPreparation(){
  if(!manifest)return;const prep=manifest.preparation||{},q=prep.questions||{},lecture=prep.lectures||{},idx=prep.index||{},retry=$('v241RetryPrep');if(retry)retry.hidden=!(manifest.source_review&&manifest.source_review.required)&&q.status!=='error'&&lecture.status!=='error'&&idx.status!=='error';
  const count=$('v241QuizCount');if(count){const available=Math.max(0,...(manifest.units||[]).map(u=>Number(u.question_count||0)));[...count.options].forEach(o=>o.disabled=Number(o.value)>available);}
  const scope=$('v241QuizUnit');if(scope){const selected=scope.value||'ready',ready=(manifest.units||[]).filter(u=>Number(u.question_count||0)>=quizCount()).length;scope.innerHTML=`<option value="ready">All ready units (${ready})</option>`+(manifest.units||[]).map((u,i)=>`<option value="${esc(u.id)}">${i+1}. ${esc(u.title)} (${Number(u.question_count||0)} questions)</option>`).join('');scope.value=selected;}
  const btn=$('v241StartQuiz');if(btn&&!btn.dataset.loading){btn.disabled=!quizReady();btn.textContent=quizReady()?'Start '+(quizCount()*quizUnits().length)+'-Question Quiz':'Waiting for reviewed questions';}
  const expand=$('v241ExpandQuiz'),chosen=(manifest.units||[]).find(u=>u.id===(scope&&scope.value));if(expand){expand.disabled=!chosen||Number(chosen.question_count||0)>=100||(Number(chosen.question_source_count||0)>0&&Number(chosen.question_processed_count||0)>=Number(chosen.question_source_count||0));expand.title=chosen?'Build additional reviewed questions for this unit':'Select one unit to build more questions';}
  const names=$('v241QuizUnits');if(names)names.innerHTML=(manifest.units||[]).map((u,i)=>`<div class="v241-case-unit">${i+1}. ${esc(u.title)}</div>`).join('');
  const body=$('v241QuizBody');if(body&&!body.dataset.error&&!quizState&&!(btn&&btn.dataset.loading))body.textContent=quizReady()?(q.status==='error'?'Ready units can be practised now. Further preparation stopped: '+String(q.error||'Unknown error')+'. Use Retry Background Preparation to add more.':'Choose one ready unit or all ready units and start now. Other chapters continue preparing in the background.'):q.status==='error'?'Question bank preparation stopped: '+String(q.error||'Unknown error')+'. Ready units remain available.':'Course Q&A is active. Practice questions are being prepared by chapter.';
  showSelectedLectureIntro();
  let note='';
  if(manifest.source_review&&manifest.source_review.required){const pending=Object.values(manifest.source_review.units||{}).flatMap(q=>q.warnings||[]),gaps=pending.some(page=>page.vision&&page.vision.ok?(page.vision.uncertain||Number(page.vision.excluded_blocks||0)>0):(!page.ocr||!page.ocr.ok||Number(page.ocr.excluded_paragraphs||0)>0))||(manifest.units||[]).some(u=>u.source_blocked);note=gaps?'部分來源頁面需檢查（'+pending.length+' 頁），請查看教材上傳紀錄。':'';}
  renderPrep(note);
  if(prep.sources&&prep.sources.status==='preparing'){if(retry)retry.disabled=true;}else if(retry)retry.disabled=false;
}
// The selected course's preparation, one row per kind of work, in the order it becomes usable.
function prepRow(label,state,detail,value,title){
  const icon={done:'✓',working:'●',waiting:'…',error:'!'}[state]||'…';
  return `<div class="v241-prep-row v241-prep-${state}"${title?` title="${esc(title)}"`:''}><span class="v241-prep-icon">${icon}</span><span class="v241-prep-label">${esc(label)}</span><span class="v241-prep-detail">${esc(detail)}</span><progress max="100" value="${Math.round(Math.max(0,Math.min(100,value||0)))}"></progress></div>`;
}
function renderPrep(note){
  const box=$('v241Prep');if(!box)return;if(!manifest){box.hidden=true;return;}
  const prep=manifest.preparation||{},units=manifest.units||[],q=prep.questions||{},lec=prep.lectures||{},idx=prep.index||{},fig=prep.figures||{},src=prep.sources||{};
  const pct=(a,b)=>100*Number(a||0)/Math.max(1,Number(b||0));
  const state=st=>st==='ready'?'done':st==='error'?'error':(!st||st==='waiting'||st==='queued'||st==='source_review')?'waiting':'working';
  const stopped='Stopped · use Retry',rows=[];
  if(src.status==='preparing')rows.push(prepRow('Re-reading PDF','working',src.total?`${Number(src.completed||0)} / ${src.total} pages`:'Working',pct(src.completed,src.total),src.message));
  rows.push(prepRow('Course Q&A','done','Ready',100));
  const ready=q.ready_units!=null?Number(q.ready_units):units.filter(u=>Number(u.question_count||0)>=2).length;
  rows.push(prepRow('Practice questions',state(q.status),q.status==='error'?stopped:`${ready} / ${units.length} units`,q.status==='ready'?100:pct(ready,units.length),q.status==='error'?q.error:q.message));
  const total=Number(lec.total_units||units.length),scripts=lec.completed_units!=null?Number(lec.completed_units):units.filter(u=>u.script_status==='ready').length;
  rows.push(prepRow('Chinese lecture scripts',state(lec.status),lec.status==='error'?stopped:`${scripts} / ${total} units`,lec.status==='ready'?100:pct(scripts,total),lec.status==='error'?lec.error:lec.message));
  rows.push(prepRow('Semantic search',state(idx.status),idx.status==='ready'?'Ready':idx.status==='error'?stopped:idx.total?Math.round(pct(idx.completed,idx.total))+'%':'Waiting',idx.status==='ready'?100:pct(idx.completed,idx.total),idx.message));
  if(fig.total)rows.push(prepRow('Slide images',fig.status==='unavailable'?'waiting':state(fig.status),fig.status==='waiting'?'After lecture scripts':fig.status==='unavailable'?'Skipped':`${Number(fig.completed||0)} / ${fig.total} pages`,fig.status==='ready'?100:pct(fig.completed,fig.total),fig.message));
  const finished=[q,lec,idx].every(x=>x.status==='ready')&&(!fig.total||['ready','unavailable'].includes(fig.status))&&src.status!=='preparing';
  const others=activity&&Number(activity.other_courses||0)+Number(activity.other_builds||0);
  const line=[note,!finished&&others?'Other courses are also preparing; the model takes turns, and the course open here goes first.':''].filter(Boolean).join(' ');
  box.innerHTML=`<div class="v241-prep-head"><b>${esc(manifest.course_name)}</b><span class="v241-prep-${finished?'done':'working'}">${finished?'Ready':'Preparing'}</span></div>${rows.join('')}${line?`<div class="v241-prep-note">${esc(line)}</div>`:''}`;
  box.hidden=false;
}
async function retryPreparation(){if(!manifest)return;const b=$('v241RetryPrep');b.disabled=true;try{const r=await fetch('/api/reference/course-retry-preparation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:manifest.course_id,model_key:modelKey()}),cache:'no-store'}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.detail||d.error||'Retry failed');manifest=d.manifest;showPreparation();}catch(e){setBuildStatus('Could not restart preparation: '+String(e&&e.message||e),'error');}finally{b.disabled=false;}}
async function expandQuizUnit(){const selected=$('v241QuizUnit'),id=selected&&selected.value,btn=$('v241ExpandQuiz');if(!manifest||!id||id==='ready'||!btn)return;btn.disabled=true;try{const r=await fetch('/api/reference/course-expand-questions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:manifest.course_id,unit_ids:[id],model_key:modelKey()}),cache:'no-store'}),d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||d.detail||'Could not request more questions');manifest=d.manifest;showPreparation();}catch(e){setBuildStatus('Could not expand this unit: '+String(e&&e.message||e),'error');}finally{showPreparation();}}
async function prioritizeUnit(kind,unitId){if(!manifest||!unitId)return;try{const r=await fetch('/api/reference/course-prioritize-unit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:manifest.course_id,unit_id:unitId,kind,model_key:modelKey()}),cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);}catch(e){setBuildStatus('Could not prioritize this unit: '+String(e&&e.message||e),'error');}}
async function refreshPreparation(){if(!manifest)return;try{const r=await fetch('/api/reference/course-status?course_id='+encodeURIComponent(manifest.course_id),{cache:'no-store'}),d=await r.json();if(!r.ok||!d.ok)return;manifest=d.manifest;activity=d.activity||null;showPreparation();}catch(_){} }
function currentUnit(){if(!manifest)return null;const id=String(($('lessonSelect')||{}).value||'');return (manifest.units||[]).find(u=>u.id===id)||manifest.units[0]||null;}
function showSelectedLectureIntro(){const u=currentUnit(),box=$('lessonText'),start=$('startLessonBtn');if(!u)return;const damaged=!!(window.CourseContent&&window.CourseContent.issues(u.script));const sourceBlocked=!!u.source_blocked;const ready=!sourceBlocked&&u.script_status==='ready'&&!!u.script&&!damaged;if(start)start.disabled=!ready;const master=window.__VQ105_INPLACE_LECTURE_MASTER__;if(box&&!(master&&master.running))box.textContent=sourceBlocked?'This unit needs source text repair or OCR. Other units remain available.':damaged?'Content needs source review. Use Retry Background Preparation.':ready?'Selected: '+u.title+'. Select “Start Lecture”.':u.script_status==='error'?'The Chinese script for '+u.title+' could not be prepared. Use Retry Background Preparation.':'Preparing the Traditional Chinese script for '+u.title+'…';}
function speechLang(text){return /[\u3400-\u9fff]/.test(String(text||''))?'zh-TW':'en-US';}
function speak(text){try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(String(text||''));u.lang=speechLang(text);u.rate=parseFloat(($('rateRange')||{}).value||'1')||1;u.pitch=parseFloat(($('pitchRange')||{}).value||'1')||1;speechSynthesis.speak(u);}catch(_){}}
function installDynamicLecture(){
  const start=$('startLessonBtn'),pause=$('pauseLessonBtn'),resume=$('resumeLessonBtn'),stop=$('stopLessonBtn'),repeat=$('repeatBtn');if(!start)return;
  start.onclick=e=>{e.preventDefault();const u=currentUnit();if(!u)return;const box=$('lessonText');if(box)box.textContent=u.script||'Lecture content is unavailable.';speak(u.script||'');if(pause)pause.disabled=false;if(stop)stop.disabled=false;if(resume)resume.disabled=true;const st=$('status');if(st)st.textContent='Status: Lecture in progress — '+u.title;};
  if(pause)pause.onclick=e=>{e.preventDefault();try{speechSynthesis.pause();}catch(_){ }pause.disabled=true;if(resume)resume.disabled=false;};
  if(resume)resume.onclick=e=>{e.preventDefault();try{speechSynthesis.resume();}catch(_){ }resume.disabled=true;if(pause)pause.disabled=false;};
  if(stop)stop.onclick=e=>{e.preventDefault();try{speechSynthesis.cancel();}catch(_){ }pause.disabled=true;resume.disabled=true;stop.disabled=true;};
  if(repeat)repeat.onclick=e=>{e.preventDefault();const u=currentUnit();if(u)speak(u.script||'');};
}
function applyVisualModule(){
  // Only the built-in default respiratory course includes this simulator.
  // Uploaded respiratory PDFs are still uploaded courses, not the default course.
  const show=!activeId()&&!manifest,tab=$('modelTab'),panel=$('modelPanel');
  const wasActive=(tab&&tab.classList.contains('active'))||(panel&&panel.classList.contains('active'));
  if(tab){tab.hidden=!show;tab.disabled=!show;tab.style.display=show?'':'none';}
  if(panel){panel.hidden=!show;panel.style.display=show?'':'none';}
  if(!show){
    if(wasActive){const lesson=$('lessonTab');if(lesson)lesson.click();}
    if(tab)tab.classList.remove('active');if(panel)panel.classList.remove('active');
    document.body.classList.remove('v150-model-active');
  }
  if(show&&tab)tab.textContent='V/Q Visual Model';
}
function installDynamicCase(){
  const panel=$('casePanel');if(!panel||!manifest)return;if(!legacyCaseHTML)legacyCaseHTML=panel.innerHTML;
  panel.innerHTML=`<div class="v241-dynamic-case"><div class="v241-case-head"><div><div class="section-label">CHAPTER-ALIGNED EXAM PRACTICE</div><h3>${esc(manifest.course_name)}</h3></div><div class="v241-quiz-controls"><label for="v241QuizUnit">Practice units</label><select id="v241QuizUnit" aria-label="Practice units"><option value="ready">All ready units</option>${(manifest.units||[]).map((u,i)=>`<option value="${esc(u.id)}">${i+1}. ${esc(u.title)}</option>`).join('')}</select><label for="v241QuizCount">Questions per unit</label><select id="v241QuizCount" aria-label="Questions per unit">${Array.from({length:10},(_,i)=>`<option value="${i+1}" ${i===1?'selected':''}>${i+1}</option>`).join('')}</select><button type="button" class="primary" id="v241StartQuiz" ${quizReady()?'':'disabled'}>Start Practice</button><button type="button" id="v241ExpandQuiz" disabled>Build more for selected unit</button></div></div><div class="v241-case-layout"><aside class="v241-case-units"><strong>${(manifest.units||[]).length} chapters</strong><div id="v241QuizUnits">${(manifest.units||[]).map((u,i)=>`<div class="v241-case-unit">${i+1}. ${esc(u.title)}</div>`).join('')}</div></aside><main class="v241-case-card"><div id="v241QuizBody">${quizReady()?'Choose ready units, then start practice.':'Course Q&A is available while the first questions prepare.'}</div></main></div></div>`;
  $('v241StartQuiz').addEventListener('click',startDynamicQuiz);
  $('v241QuizCount').addEventListener('change',updateQuizCount);
  $('v241QuizUnit').addEventListener('change',()=>{updateQuizCount();const id=$('v241QuizUnit').value,u=(manifest.units||[]).find(x=>x.id===id);if(u&&Number(u.question_count||0)<quizCount())prioritizeUnit('questions',id);});
  $('v241ExpandQuiz').addEventListener('click',expandQuizUnit);
  $('v241QuizCount').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();if(!$('v241StartQuiz').disabled)startDynamicQuiz();}});
}
function quizCount(){return Math.max(1,Math.min(10,Number(($('v241QuizCount')||{}).value)||2));}
function updateQuizCount(){showPreparation();}
async function startDynamicQuiz(){
  const btn=$('v241StartQuiz');if(btn.dataset.loading||!quizReady())return;const selected=quizUnits().map(u=>u.id);btn.disabled=true;btn.dataset.loading='1';const body=$('v241QuizBody'),per=quizCount();quizState=null;delete body.dataset.error;body.textContent='Drawing '+per+' ready questions per selected unit…';
  try{const r=await fetch('/api/reference/course-quiz',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:manifest.course_id,per_unit:per,unit_ids:selected}),cache:'no-store'}),result=await r.json();if(!r.ok||!result.ok||!result.quiz_id)throw new Error(result.detail||result.error||'quiz unavailable');if(window.CourseContent&&(result.questions||[]).some(q=>!window.CourseContent.questionOk(q)))throw new Error('Question text needs source review. Retry preparation.');quizState={quizId:result.quiz_id,items:result.questions||[],index:0,answers:{},submitted:{},generation:result.generation||{}};renderDynamicQuestion();}
  catch(e){body.dataset.error='1';body.textContent='Scenario Practice could not start: '+String(e&&e.message||e);}
  finally{delete btn.dataset.loading;btn.disabled=!quizReady();}
}
function renderDynamicQuestion(){
  if(!quizState)return;const body=$('v241QuizBody'),items=quizState.items;if(!items.length){body.textContent='No validated scenario questions were returned.';return;}
  const priorNav=body.querySelector('#v241QuestionNav'),scroll=priorNav?priorNav.scrollLeft:0;
  const i=quizState.index,q=items[i],chosen=quizState.answers[i],done=quizState.submitted[i],letters=['A','B','C','D'];
  const jumpOptions=items.map((item,n)=>`<option value="${n}" ${n===i?'selected':''}>${n+1}. ${esc(item.unit_title)}${quizState.submitted[n]?' · answered':quizState.answers[n]!==undefined?' · selected':''}</option>`).join('');
  const buttons=items.map((item,n)=>{const state=quizState.submitted[n]?'answered':quizState.answers[n]!==undefined?'selected':'';return `<button type="button" class="${state}${n===i?' current':''}" data-v241-question="${n}" aria-label="Question ${n+1}${state?' '+state:' unanswered'}" aria-current="${n===i?'step':'false'}" title="Question ${n+1} · ${esc(item.unit_title)}">${n+1}</button>`;}).join('');
  body.innerHTML=`<div class="v241-question-navigation"><div class="v241-question-jump"><span id="v241QuizProgress" aria-live="polite"></span><label for="v241QuestionJump">Go to question</label><select id="v241QuestionJump" aria-label="Go to question">${jumpOptions}</select></div><div class="v241-question-nav" id="v241QuestionNav" role="navigation" aria-label="Quiz question numbers">${buttons}</div></div><div class="v241-qmeta">Question ${i+1} of ${items.length} · ${esc(q.unit_title)}</div><div class="v241-qstem">${esc(q.stem)}</div>${(q.choices||[]).map((c,n)=>`<label class="v241-choice"><input type="radio" name="v241Choice" value="${n}" ${String(chosen)===String(n)?'checked':''} ${done?'disabled':''}> <strong>${letters[n]}.</strong> ${esc(c)}</label>`).join('')}<div class="v241-feedback" id="v241Feedback" ${done?'':'hidden'}></div><div class="v241-nav"><button type="button" id="v241Prev" ${i===0?'disabled':''}>Previous</button><button type="button" id="v241Submit" ${done?'disabled':''}>Submit Answer</button><button type="button" id="v241Next" ${i===items.length-1?'disabled':''}>Next</button><button type="button" id="v241Finish">Finish &amp; View Results</button></div>`;
  const nav=$('v241QuestionNav');nav.scrollLeft=scroll;
  nav.querySelectorAll('button[data-v241-question]').forEach(button=>button.onclick=()=>jumpDynamicQuestion(Number(button.dataset.v241Question)));
  $('v241QuestionJump').onchange=e=>jumpDynamicQuestion(Number(e.target.value));
  [...body.querySelectorAll('input[name="v241Choice"]')].forEach(x=>x.addEventListener('change',()=>{quizState.answers[i]=Number(x.value);updateDynamicNavigation();}));
  $('v241Prev').onclick=()=>jumpDynamicQuestion(i-1);$('v241Next').onclick=()=>jumpDynamicQuestion(i+1);$('v241Submit').onclick=()=>submitDynamicAnswer();$('v241Finish').onclick=finishDynamicQuiz;
  if(window.CourseMath)window.CourseMath.typeset(body);
  updateDynamicNavigation();if(done)showDynamicFeedback();
}
function jumpDynamicQuestion(index){if(!quizState||!Number.isInteger(index)||index<0||index>=quizState.items.length)return;quizState.index=index;renderDynamicQuestion();}
function updateDynamicNavigation(){
  if(!quizState)return;const total=quizState.items.length,answered=Object.keys(quizState.submitted).length,selected=Object.keys(quizState.answers).length;
  const progress=$('v241QuizProgress');if(progress)progress.textContent=answered+' / '+total+' answered'+(selected>answered?' · '+(selected-answered)+' selected':'');
  const button=document.querySelector('#v241QuestionNav button[data-v241-question="'+quizState.index+'"]');
  if(button){const state=quizState.submitted[quizState.index]?'answered':quizState.answers[quizState.index]!==undefined?'selected':'';button.className=state+' current';button.setAttribute('aria-label','Question '+(quizState.index+1)+(state?' '+state:' unanswered'));}
  const option=$('v241QuestionJump')&&$('v241QuestionJump').options[quizState.index];if(option)option.text=(quizState.index+1)+'. '+String(quizState.items[quizState.index].unit_title||'')+(quizState.submitted[quizState.index]?' · answered':quizState.answers[quizState.index]!==undefined?' · selected':'');
}
function submitDynamicAnswer(){const i=quizState.index;if(quizState.answers[i]===undefined){$('v241Feedback').hidden=false;$('v241Feedback').textContent='Select one answer first.';return;}quizState.submitted[i]=true;renderDynamicQuestion();}
function showDynamicFeedback(){const i=quizState.index,q=quizState.items[i],chosen=Number(quizState.answers[i]),correct=Number(q.answer),fb=$('v241Feedback'),letters=['A','B','C','D'];if(!fb)return;fb.hidden=false;fb.textContent=(chosen===correct?'Correct.':'Incorrect.')+'\nCorrect answer: '+letters[correct]+'. '+String((q.choices||[])[correct]||'')+(q.explanation?'\n'+q.explanation:'');if(window.CourseMath)window.CourseMath.typeset(fb);}
async function finishDynamicQuiz(){
  if(!quizState||quizState.finished)return;const state=quizState,missing=state.items.filter((_,i)=>state.answers[i]===undefined).length;
  if(missing&&!window.confirm(missing+' question(s) are unanswered. Submit this quiz anyway?'))return;
  const btn=$('v241Finish');if(btn)btn.disabled=true;
  try{const answers=state.items.map((q,i)=>({id:q.id,choice:state.answers[i]===undefined?null:state.answers[i]}));const r=await fetch('/api/reference/course-quiz-result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:manifest.course_id,quiz_id:state.quizId,answers}),cache:'no-store'}),report=await r.json().catch(()=>({}));if(!r.ok||!report.ok)throw new Error(report.error||report.detail||'Results unavailable');state.finished=true;let aggregate=null;try{const s=await fetch('/api/reference/course-quiz-stats?course_id='+encodeURIComponent(manifest.course_id),{cache:'no-store'});if(s.ok)aggregate=await s.json();}catch(_){}renderQuizSummary(report,aggregate);}
  catch(e){const fb=$('v241Feedback');if(fb){fb.hidden=false;fb.textContent='Could not save quiz result: '+String(e&&e.message||e);}if(btn)btn.disabled=false;}
}
function renderQuizSummary(report,aggregate){
  const items=quizState.items,letters=['A','B','C','D'],rows=items.map((q,i)=>{const graded=report.items[i],chosen=graded.choice===null?'Not answered':letters[graded.choice]+'. '+q.choices[graded.choice],correct=letters[q.answer]+'. '+q.choices[q.answer];return `<div class="v241-summary-item"><strong>Question ${i+1} · ${esc(q.unit_title)} · ${graded.correct?'Correct':graded.choice===null?'Unanswered':'Incorrect'}</strong><div>${esc(q.stem)}</div><div>Your answer: ${esc(chosen)}</div><div>Correct answer: ${esc(correct)}</div><div>${esc(q.explanation||'')}</div></div>`;}).join('');
  const totals=aggregate&&aggregate.ok?`<p>Course statistics: ${aggregate.attempts} completed quiz(es), ${aggregate.correct} / ${aggregate.questions} correct (${aggregate.accuracy}%).</p>`:'';
  const unitNames=Object.fromEntries(items.map(q=>[q.unit_id,q.unit_title]));
  const byUnit=(report.units||[]).map(u=>`<li>${esc(unitNames[u.unit_id]||u.unit_id)}: ${u.correct} / ${u.total} correct</li>`).join('');
  $('v241QuizBody').innerHTML=`<div class="v241-summary"><h4>Quiz Results</h4><p>${report.correct} / ${report.total} correct (${report.accuracy}%) · ${report.answered} answered</p>${totals}${byUnit?'<ul class="v241-unit-results">'+byUnit+'</ul>':''}<p>Random draw from the prepared bank: ${quizState.generation.prebuilt_questions||items.length} questions · ${quizState.generation.model_calls||0} model calls · ${quizState.generation.elapsed_ms||0} ms.</p>${rows}<button type="button" id="v241NewQuiz">Start another quiz</button></div>`;
  if(window.CourseMath)window.CourseMath.typeset($('v241QuizBody'));$('v241NewQuiz').onclick=()=>startDynamicQuiz();
}

async function pollCourseAnswerJob(id,request){
  const began=Date.now(),manager=window.VQ_QA_REQUESTS;
  while(Date.now()-began<900000){
    if(request&&manager&&!manager.isCurrent(request))throw new Error('Superseded question');
    const r=await fetch('/api/reference/job-status?id='+encodeURIComponent(id),{
      cache:'no-store',signal:request&&request.controller?request.controller.signal:undefined});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.error||('HTTP '+r.status));
    if(d.status==='done')return d.result||{};
    if(d.status==='cancelled')throw new Error('Question cancelled after model switch');
    if(d.status==='error')throw new Error(d.error||'Local answer failed');
    await new Promise(resolve=>setTimeout(resolve,1500));
  }
  throw new Error('The local course answer timed out.');
}
async function courseAnswerOne(q,request){
  const cloud=cloudCfg(),lang=qLang(q);window.VQ_LAST_DETECTED_LANGUAGE=lang;if(cloud.provider!=='off'&&!cloud.apiKey)return 'Please enter the API Key and press Enter or “Confirm API Key” before using the selected cloud model.';
  const path=cloud.provider==='off'?'/api/reference/course-answer-job-start':'/api/reference/course-answer';
  const payload={course_id:manifest.course_id,question:String(q||''),model_key:modelKey(),language_tag:lang,cloud_provider:cloud.provider};if(cloud.provider!=='off'){payload.cloud_api_key=cloud.apiKey;payload.cloud_model=cloud.model;}
  // Keep the small job-start request alive long enough to learn its ID even if the user switches models.
  // This closes the race where aborting job-start creates a job that cannot later be cancelled.
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),cache:'no-store',
    signal:cloud.provider==='off'?undefined:(request&&request.controller?request.controller.signal:undefined)});
  let d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.detail||d.error||('HTTP '+r.status));
  if(cloud.provider==='off'){
    if(!d.job_id)throw new Error('Local course answer job was not created.');
    if(request&&window.VQ_QA_REQUESTS)window.VQ_QA_REQUESTS.rememberJob(request,d.job_id);
    if(request&&window.VQ_QA_REQUESTS&&!window.VQ_QA_REQUESTS.isCurrent(request))throw new Error('Superseded question');
    d=await pollCourseAnswerJob(d.job_id,request);
  }
  if(request&&window.VQ_QA_REQUESTS&&!window.VQ_QA_REQUESTS.isCurrent(request))throw new Error('Superseded question');
  window.VQ_LAST_COURSE_ANSWER_META={course_id:manifest.course_id,engine:d.engine||'local-course-rag',
    sources:Array.isArray(d.sources)?d.sources:[],correction_id:d.correction_id||''};
  const answer=window.CourseContent?window.CourseContent.clean(d.answer):String(d.answer||'').trim();
  if(!answer)throw new Error('The course answer service returned no answer.');return answer;
}
function installAnswerAdapter(){
  if(!manifest)return;const current=window.VQ_generateFinalAnswer;if(!legacyAnswer&&typeof current==='function')legacyAnswer=current;if(current&&current.__v241CourseAdapter)return;
  async function checkedAnswer(question,request){
    let response='';
    for(let attempt=0;attempt<2;attempt++){
      if(request&&window.VQ_QA_REQUESTS&&!window.VQ_QA_REQUESTS.isCurrent(request))return {answer:'',ok:false,obsolete:true};
      window.VQ_LAST_COURSE_ANSWER_META=null;
      try{
        response=await courseAnswerOne(question,request);
        if(window.VQ_cleanStudentAnswer)response=window.VQ_cleanStudentAnswer(response);
        const engine=String(window.VQ_LAST_COURSE_ANSWER_META&&window.VQ_LAST_COURSE_ANSWER_META.engine||'');
        const terminal=/^(?:course-scope-rejected|course-no-evidence)$/.test(engine);
        if(String(response||'').trim()&&(terminal||!window.VQ_validateStudentAnswer||window.VQ_validateStudentAnswer(question,response).ok))return {answer:response,ok:true};
      }catch(e){if(request&&window.VQ_QA_REQUESTS&&!window.VQ_QA_REQUESTS.isCurrent(request))return {answer:'',ok:false,obsolete:true};response='';}
    }
    return {answer:/^zh/i.test(qLang(question))?'此題尚未完成：未取得完整且格式有效的答案，請重試此題。':'This question is unfinished. Please retry this question.',ok:false};
  }
  const fn=async function(raw){
    if(!manifest&&legacyAnswer)return legacyAnswer.call(window,raw);
    const started=performance.now(),question=String(raw||'').trim();let items=[];
    const request=window.VQ_QA_REQUESTS&&window.VQ_QA_REQUESTS.current();
    const current=()=>!request||!window.VQ_QA_REQUESTS||window.VQ_QA_REQUESTS.isCurrent(request);
    try{items=(window.VQ_splitQuestionBatch&&window.VQ_splitQuestionBatch(question))||[question];}catch(_){items=[question];}
    if(!Array.isArray(items)||!items.length)items=[question];
    const out=[],rows=[],batch=items.length>=2;
    for(let i=0;i<items.length;i++){
      if(!current())return '';
      const box=$('teacherAnswer');if(batch&&box)box.textContent=out.concat('Processing uploaded-course batch: '+(i+1)+' / '+items.length+'…').join('\n\n');
      const result=await checkedAnswer(items[i],request);if(!current())return '';rows.push({id:i+1,question:items[i],answer:result.answer,status:result.ok?'ok':'error'});
      out.push(batch?'Question '+(i+1)+'\n'+result.answer:result.answer);if(box&&current())box.textContent=out.join('\n\n');
    }
    if(!current())return '';
    const completed=rows.filter(row=>row.status==='ok').length;
    window.VQ_LAST_BATCH_META={requested:items.length,completed,failed:items.length-completed,answers:rows};
    const answer=out.join('\n\n'),elapsed=performance.now()-started;
    setTimeout(()=>{if(typeof window.VQ_showAnswerFeedback==='function')window.VQ_showAnswerFeedback(question,answer,elapsed);},80);return answer;
  };fn.__v241CourseAdapter=true;window.VQ_generateFinalAnswer=fn;adapterInstalled=true;
}

async function restoreDraft(){
  try{draft=await draftIO('readonly');}catch(e){if(pendingStage())setBuildStatus('Saved PDF data could not be read: '+String(e.message||e)+'. Select the PDFs again.','error');return;}
  if(!draft)return;
  if(draft.courseName)$('v241CourseName').value=draft.courseName;
  if(draft.phase==='uploading'){
    setBuildStatus('Reconnecting to the saved upload…');
    try{await resumeUpload();}catch(e){setBuildStatus('Upload paused: '+String(e.message||e)+'. Select Upload & Inspect to start a new upload, or refresh to retry.','error');}
  }else if(draft.phase==='inspected'){
    inspection=draft.inspection;renderInspection();$('v241BuildBtn').disabled=false;setBuildStatus('Saved PDF inspection restored. Review the teaching units and build.','ok');
  }else if(draft.phase==='building'||pendingStage()===draft.stageId){
    try{
      const {r,d}=await getJSON('/api/reference/course-stage-status?stage_id='+encodeURIComponent(draft.stageId),'Reconnecting to the build');if(!r.ok||!d.ok)throw new Error(d.error||'Stage status unavailable');
      if(d.job)await pollBuild(d.job.id);
      else if(draft.buildBody){const start=await fetch('/api/reference/course-build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft.buildBody),cache:'no-store'}),job=await start.json();if(!start.ok||!job.ok)throw new Error(job.error||'Build request failed');await pollBuild(job.job.id);}
    }catch(e){setBuildStatus('Build is saved, but reconnect failed: '+String(e.message||e)+'. Refresh to retry.','error');}
  }
}
async function init(){injectBuilder();applyVisualModule();if(!legacyCaseHTML&&$('casePanel'))legacyCaseHTML=$('casePanel').innerHTML;await useAccountKeys();await loadCourseList();const cid=activeId();if(cid)await loadManifest(cid);await restoreDraft();
  if(await loadBuilds()!=='stop'){const timer=setInterval(async()=>{if(await loadBuilds()==='stop')clearInterval(timer);},5000);}try{const notice=sessionStorage.getItem(READY_NOTICE);if(notice&&manifest){sessionStorage.removeItem(READY_NOTICE);setBuildStatus(notice,'ok');}}catch(_){}setInterval(refreshPreparation,4500);setTimeout(()=>{if(manifest)installAnswerAdapter();},2800);setTimeout(()=>{if(manifest)installAnswerAdapter();},4200);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
