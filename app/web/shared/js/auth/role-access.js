/* R12 accounts and roles: signed-in user bar, student/teacher interface, course codes, account review (administrator). */
(function(){
'use strict';
const $=id=>document.getElementById(id);
const ACTIVE_KEY='VQ_V241_ACTIVE_COURSE_ID';
const ROLE={student:'學生 Student',teacher:'老師 Teacher'};
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let user=null,redirecting=false;

function toLogin(){
  if(redirecting)return;redirecting=true;
  location.replace('/login/?next='+encodeURIComponent(location.pathname+location.search));
}
// A session that ends while the page is open (sign-out elsewhere, account disabled) returns to the sign-in page.
const nativeFetch=window.fetch.bind(window);
window.fetch=async function(input,init){
  const r=await nativeFetch(input,init);
  try{
    const url=typeof input==='string'?input:(input&&input.url)||'';
    if(r.status===401&&/\/api\//.test(url)&&!/\/api\/auth\//.test(url))r.clone().json().then(d=>{if(d&&d.code==='login_required')toLogin();}).catch(()=>{});
    if(r.status===409&&/\/api\//.test(url))r.clone().json().then(d=>{if(d&&d.code==='site_moved'&&/^https:\/\//.test(d.url||''))location.replace(new URL(location.pathname+location.search,d.url).toString());}).catch(()=>{});
  }catch(_){}
  return r;
};

function css(){
  if($('vqRoleCss'))return;const s=document.createElement('style');s.id='vqRoleCss';
  s.textContent=`
body:not(.vq-role-teacher):not(.vq-role-student) #v241CourseBuilder{visibility:hidden}
.vq-userbar{display:flex;justify-content:flex-end;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 10px;font-size:13px}
.vq-userbar .vq-who{display:inline-flex;align-items:center;gap:8px;background:#fff;border:1px solid #c8d9ec;border-radius:999px;padding:5px 6px 5px 12px;color:#1d3550}
.vq-userbar .vq-role{font-weight:800;border-radius:999px;padding:3px 9px;font-size:12px}
.vq-role-teacher .vq-userbar .vq-role{background:#e0f2ef;color:#0f5e57}.vq-role-student .vq-userbar .vq-role{background:#e3effa;color:#185a91}
.vq-userbar button{font:inherit;font-size:12.5px!important;font-weight:700!important;border:1px solid #c8d9ec!important;background:#fff;color:#174d7f;border-radius:999px!important;padding:6px 12px!important;box-shadow:none!important;cursor:pointer}
.vq-userbar button:hover{background:#f1f7fd}.vq-userbar .vq-badge{display:inline-block;min-width:18px;padding:0 5px;margin-left:5px;border-radius:9px;background:#d9534f;color:#fff;font-size:11px;line-height:18px;text-align:center}
.vq-role-student #v241CourseBuilder .v241-grid,.vq-role-student #v241InspectBtn,.vq-role-student #v241BuildBtn,.vq-role-student #v241RetryPrep,
.vq-role-student #v241WorkProgress,.vq-role-student #v241UnitWrap,.vq-role-student #v241DeleteCourse,.vq-role-student #v241ExpandQuiz,
.vq-role-student #v241BuildStatus{display:none!important}
.vq-role-student #v241CourseBuilder .v241-actions:empty{display:none}
.vq-join{display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-top:12px;border-top:1px solid #dbe7f2;padding-top:12px}
.vq-join .v241-field{flex:0 1 220px}.vq-join input{width:100%;box-sizing:border-box;border:1px solid #b8cce1;border-radius:9px;background:#fff;padding:10px;font:inherit;text-transform:uppercase;letter-spacing:.12em}
.vq-join button,.vq-code button{min-height:40px;padding:8px 14px!important;border-radius:9px!important;border:1px solid #8fb4d9!important;background:#fff;color:#174d7f;font-weight:800!important;font-size:14px!important;box-shadow:none!important;cursor:pointer}
.vq-join .vq-join-msg{flex-basis:100%;font-size:13px;color:#42617e}.vq-join .vq-join-msg.err{color:#8f2f2f}
.vq-code{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px;padding:10px 12px;border-radius:10px;background:#eef8f6;border:1px solid #cde8e3;color:#1e4d47;font-size:13px}
.vq-code select{width:auto!important;flex:0 1 auto;border:1px solid #b8cce1;border-radius:8px;padding:5px 8px;font:inherit;background:#fff;color:#173f69;max-width:100%}
.vq-code b{font-size:18px;letter-spacing:.18em;color:#0f5e57;font-family:ui-monospace,Menlo,Consolas,monospace}
.vq-modal{position:fixed;inset:0;background:rgba(15,35,60,.45);display:grid;place-items:center;z-index:99999;padding:16px}
.vq-modal>div{background:#fff;border-radius:16px;max-width:900px;width:100%;max-height:86vh;overflow:auto;padding:20px;box-shadow:0 20px 50px rgba(0,0,0,.25);color:#1d3550}
.vq-modal h3{margin:0 0 4px;font-size:19px}.vq-modal .vq-sub{color:#5b7087;font-size:13px;margin-bottom:14px}
.vq-modal table{width:100%;border-collapse:collapse;font-size:13px}.vq-modal th,.vq-modal td{text-align:left;padding:8px 6px;border-bottom:1px solid #e4edf5;vertical-align:top}
.vq-modal th{color:#5b7087;font-weight:700;font-size:12px}.vq-modal td small{color:#6b7f93}
.vq-modal .vq-acts{display:flex;gap:5px;flex-wrap:wrap}.vq-modal .vq-acts button,.vq-modal .vq-close,.vq-modal form button{font:inherit;font-size:12px!important;font-weight:700!important;border:1px solid #c8d9ec!important;background:#fff;color:#174d7f;border-radius:8px!important;padding:5px 9px!important;box-shadow:none!important;cursor:pointer;white-space:nowrap}
.vq-modal .vq-acts .s{border-color:#1f6fb2!important;color:#1f6fb2}.vq-modal .vq-acts .t{border-color:#0f766e!important;color:#0f766e}.vq-modal .vq-acts .r{border-color:#e3b4b4!important;color:#a12d2d}
.vq-pill{display:inline-block;border-radius:999px;padding:2px 8px;font-size:11.5px;font-weight:800}.vq-pill.pending{background:#fff3d6;color:#7a4b00}.vq-pill.approved{background:#e8f6ee;color:#185c37}.vq-pill.rejected,.vq-pill.disabled{background:#f3e8e8;color:#8f2f2f}
.vq-modal-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.vq-modal form label{display:block;font-size:13px;font-weight:700;margin:10px 0 4px}.vq-modal form label.vq-showpw{display:flex;align-items:center;gap:7px;font-weight:600;color:#42617e;cursor:pointer}.vq-modal form label.vq-showpw input{width:auto;margin:0}.vq-modal form input{width:100%;box-sizing:border-box;border:1px solid #b8cce1;border-radius:9px;padding:9px;font:inherit}
.vq-modal .vq-note{margin-top:10px;font-size:13px}.vq-modal .vq-note.err{color:#8f2f2f}.vq-modal .vq-note.ok{color:#185c37}
@media(max-width:640px){.vq-modal table thead{display:none}.vq-modal table,.vq-modal tbody,.vq-modal tr,.vq-modal td{display:block;width:100%}.vq-modal tr{border-bottom:1px solid #dbe7f2;padding:6px 0}.vq-modal td{border:0;padding:3px 0}}
`;document.head.appendChild(s);
}

function modal(inner){
  const m=document.createElement('div');m.className='vq-modal';m.innerHTML='<div role="dialog" aria-modal="true">'+inner+'</div>';
  m.addEventListener('click',e=>{if(e.target===m||e.target.closest('.vq-close'))m.remove();});
  document.body.appendChild(m);return m;
}

async function signOut(){
  try{await nativeFetch('/api/auth/logout',{method:'POST',cache:'no-store'});}catch(_){}
  try{localStorage.removeItem(ACTIVE_KEY);}catch(_){}
  location.replace('/login/?signedout=1');
}

function changePassword(){
  const m=modal(`<div class="vq-modal-head"><div><h3>修改密碼</h3><div class="vq-sub">Change password</div></div><button class="vq-close" type="button">關閉</button></div>
  <form><label>目前密碼</label><input type="password" name="old" autocomplete="current-password" autocapitalize="none" autocorrect="off" spellcheck="false" required><label>新密碼（至少 8 個字元）</label><input type="password" name="new" autocomplete="new-password" autocapitalize="none" autocorrect="off" spellcheck="false" minlength="8" required>
  <label class="vq-showpw"><input type="checkbox" name="show"> 顯示密碼 Show password</label>
  <div style="margin-top:14px"><button type="submit">儲存 Save</button></div><div class="vq-note"></div></form>`);
  const f=m.querySelector('form'),note=m.querySelector('.vq-note');
  f.show.addEventListener('change',()=>{const type=f.show.checked?'text':'password';f.old.type=type;f.new.type=type;});
  f.addEventListener('submit',async e=>{e.preventDefault();note.className='vq-note';note.textContent='儲存中…';
    const r=await nativeFetch('/api/auth/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old_password:f.old.value,new_password:f.new.value})});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){note.className='vq-note ok';note.textContent='密碼已更新。';f.reset();f.old.type=f.new.type='password';}else{note.className='vq-note err';note.textContent=d.error||'無法更新密碼。';}});
}

function fmt(t){if(!t)return '—';const d=new Date(t*1000);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
async function accountsPanel(){
  const m=modal(`<div class="vq-modal-head"><div><h3>帳號審核與管理</h3><div class="vq-sub">新申請也會寄到管理員信箱。核准後對方即可登入。</div></div><button class="vq-close" type="button">關閉</button></div><div class="vq-list">載入中…</div><div class="vq-note"></div>`);
  const list=m.querySelector('.vq-list'),note=m.querySelector('.vq-note');
  const btn=(a,cls,label,u)=>`<button class="${cls}" data-a="${a}" data-u="${u.id}">${label}</button>`;
  function actions(u){
    if(u.is_admin)return '';
    if(u.status==='pending')return btn('student','s','核准為學生',u)+btn('teacher','t','核准為老師',u)+btn('reject','r','不核准',u);
    if(u.status==='approved')return (u.role==='teacher'?btn('student','s','改為學生',u):btn('teacher','t','改為老師',u))+btn('disable','r','停用',u);
    return btn('student','s','核准為學生',u)+btn('teacher','t','核准為老師',u);
  }
  async function load(){
    const r=await nativeFetch('/api/auth/admin/users',{cache:'no-store'}),d=await r.json().catch(()=>({}));
    if(!r.ok){list.textContent=d.error||'無法載入帳號清單。';return;}
    list.innerHTML='<table><thead><tr><th>姓名 / Email</th><th>申請身分</th><th>狀態</th><th>說明</th><th>申請日 / 最近登入</th><th>操作</th></tr></thead><tbody>'+
      d.users.map(u=>`<tr><td><b>${esc(u.name)}</b>${u.is_admin?' <small>(管理員)</small>':''}<br><small>${esc(u.email)}</small></td><td>${esc(ROLE[u.requested_role]||u.requested_role)}</td>
      <td><span class="vq-pill ${esc(u.status)}">${esc({pending:'審核中',approved:'已核准',rejected:'未核准',disabled:'已停用'}[u.status]||u.status)}</span>${u.status==='approved'?'<br><small>'+esc(ROLE[u.role]||u.role)+'</small>':''}${u.courses?'<br><small>'+u.courses+' 門課程</small>':''}</td>
      <td><small>${esc(u.note||'—')}</small></td><td><small>${fmt(u.created_at)}<br>${fmt(u.last_login)}</small></td>
      <td><div class="vq-acts">${actions(u)}<button data-a="reset" data-u="${u.id}">重設密碼</button></div></td></tr>`).join('')+'</tbody></table>';
  }
  list.addEventListener('click',async e=>{
    const b=e.target.closest('button[data-a]');if(!b)return;const a=b.dataset.a,id=Number(b.dataset.u);b.disabled=true;note.className='vq-note';note.textContent='處理中…';
    const path=a==='reset'?'/api/auth/admin/reset-password':'/api/auth/admin/decide';
    const r=await nativeFetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:id,action:a})}),d=await r.json().catch(()=>({}));
    if(!r.ok||!d.ok){note.className='vq-note err';note.textContent=d.error||'操作失敗。';b.disabled=false;return;}
    note.className='vq-note ok';note.textContent=a==='reset'?'臨時密碼：'+d.temporary_password+'（請轉告使用者，登入後可自行修改密碼）':'已更新。'+(a==='student'||a==='teacher'?' 請告知對方可以登入。':'');
    await load();refreshPending();
  });
  load();
}
async function refreshPending(){
  const b=$('vqAccountsBtn');if(!b)return;
  try{const r=await nativeFetch('/api/auth/admin/users',{cache:'no-store'}),d=await r.json();const n=(d.users||[]).filter(u=>u.status==='pending').length;
    b.innerHTML='帳號審核'+(n?'<span class="vq-badge">'+n+'</span>':'');}catch(_){}
}

function userBar(){
  const app=document.querySelector('.app');if(!app||$('vqUserBar'))return;
  const bar=document.createElement('div');bar.id='vqUserBar';bar.className='vq-userbar';
  bar.innerHTML=`<span class="vq-who"><span>${esc(user.name)}</span><span class="vq-role">${esc(ROLE[user.role]||user.role)}</span></span>`+
    (user.is_admin?'<button type="button" id="vqAccountsBtn">帳號審核</button>':'')+
    '<button type="button" id="vqPasswordBtn">修改密碼</button><button type="button" id="vqLogoutBtn">登出 Sign out</button>';
  app.insertBefore(bar,app.firstChild);
  $('vqLogoutBtn').addEventListener('click',signOut);$('vqPasswordBtn').addEventListener('click',changePassword);
  if(user.is_admin){$('vqAccountsBtn').addEventListener('click',accountsPanel);refreshPending();}
}

function studentBuilder(b){
  const h=b.querySelector('.v241-head h2'),sub=b.querySelector('.v241-head .v241-sub');
  if(h)h.textContent='我的課程 My Courses';
  if(sub)sub.textContent='預設為內建生理學課程。若老師提供課程代碼，輸入後即可使用老師上傳的課程（講解、AI 助理問答與練習題）。';
  const label=b.querySelector('label[for="v241ExistingCourses"]');if(label)label.textContent='目前課程 Current course';
  const note=b.querySelector('.v241-active+.v241-sub');if(note)note.hidden=true;
  if($('vqJoin'))return;
  const row=document.createElement('div');row.id='vqJoin';row.className='vq-join';
  row.innerHTML='<div class="v241-field"><label for="vqJoinCode">課程代碼 Course code</label><input id="vqJoinCode" maxlength="12" autocomplete="off" placeholder="例如 7K3QXP"></div><button type="button" id="vqJoinBtn">加入課程 Join</button><div class="vq-join-msg" id="vqJoinMsg"></div>';
  const active=b.querySelector('.v241-active');(active||b).insertAdjacentElement('afterend',row);
  const go=async()=>{const code=$('vqJoinCode').value.trim(),msg=$('vqJoinMsg');if(!code){msg.className='vq-join-msg err';msg.textContent='請輸入老師提供的課程代碼。';return;}
    $('vqJoinBtn').disabled=true;msg.className='vq-join-msg';msg.textContent='加入中…';
    try{const r=await nativeFetch('/api/auth/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})}),d=await r.json().catch(()=>({}));
      if(r.ok&&d.ok){msg.textContent='已加入，正在開啟課程…';try{localStorage.setItem(ACTIVE_KEY,d.course_id);}catch(_){}location.reload();return;}
      msg.className='vq-join-msg err';msg.textContent=d.error||'無法加入課程。';}catch(_){msg.className='vq-join-msg err';msg.textContent='無法連線到伺服器。';}
    $('vqJoinBtn').disabled=false;};
  $('vqJoinBtn').addEventListener('click',go);$('vqJoinCode').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();go();}});
}

async function teacherBuilder(b){
  const sub=b.querySelector('.v241-active+.v241-sub');
  if(sub)sub.textContent=user.is_admin?'管理員可管理所有老師的課程：刪除、代老師上傳、轉移擁有者。選擇課程後把課程代碼提供給學生。內建課程不能刪除。':'只有您看得到自己上傳的課程；其他老師看不到。選擇課程後把課程代碼提供給學生，學生輸入代碼即可使用該課程。內建課程不能刪除。';
  let codes={},courses={};
  try{const r=await nativeFetch('/api/reference/course-list',{cache:'no-store'}),d=await r.json();(d.courses||[]).forEach(c=>{courses[c.course_id]=c;if(c.access_code)codes[c.course_id]=c.access_code;});}catch(_){}
  const sel=$('v241ExistingCourses');
  const render=()=>{let box=$('vqCourseCode');const cid=sel&&sel.value,code=cid&&codes[cid];
    if(!code){if(box)box.remove();return;}
    if(!box){box=document.createElement('div');box.id='vqCourseCode';box.className='vq-code';(b.querySelector('.v241-active')||b).insertAdjacentElement('afterend',box);}
    box.innerHTML='學生課程代碼 Course code：<b>'+esc(code)+'</b><button type="button">複製</button><span>學生登入後輸入此代碼即可使用這門課。</span>';
    box.querySelector('button').addEventListener('click',e=>{try{navigator.clipboard.writeText(code);e.target.textContent='已複製';}catch(_){}});};
  render();if(sel)sel.addEventListener('change',render);
  if(user.is_admin)adminCourseTools(b,courses);
}

// Administrator: build a course for another teacher, and move a saved course to another teacher.
async function adminCourseTools(b,courses){
  let people=[];
  try{const r=await nativeFetch('/api/auth/admin/users',{cache:'no-store'}),d=await r.json();people=(d.users||[]).filter(u=>u.status==='approved'&&(u.role==='teacher'||u.is_admin));}catch(_){}
  if(!people.length)return;
  const options=pick=>people.map(u=>`<option value="${u.id}"${u.id===pick?' selected':''}>${esc(u.name)} · ${esc(u.email)}</option>`).join('');
  const grid=b.querySelector('.v241-grid');
  if(grid&&!$('vqBuildOwner')){const f=document.createElement('div');f.className='v241-field';f.innerHTML='<label for="vqBuildOwner">Course Owner</label><select id="vqBuildOwner">'+options(user.id)+'</select>';grid.appendChild(f);}
  const sel=$('v241ExistingCourses');
  const render=()=>{let box=$('vqCourseOwner');const c=courses[sel&&sel.value];
    if(!c){if(box)box.remove();return;}
    if(!box){box=document.createElement('div');box.id='vqCourseOwner';box.className='vq-code';($('vqCourseCode')||b.querySelector('.v241-active')||b).insertAdjacentElement('afterend',box);}
    box.innerHTML='課程擁有者 Owner：<select aria-label="Course owner">'+options(c.owner_id)+'</select><span></span>';
    const pick=box.querySelector('select'),msg=box.querySelector('span');
    pick.addEventListener('change',async()=>{
      const id=Number(pick.value),person=people.find(u=>u.id===id)||{};
      if(!window.confirm('把「'+c.course_name+'」轉給 '+person.name+'？')){pick.value=String(c.owner_id);return;}
      pick.disabled=true;msg.textContent='處理中…';
      try{const r=await nativeFetch('/api/auth/admin/course-owner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({course_id:c.course_id,user_id:id})}),d=await r.json().catch(()=>({}));
        if(!r.ok||!d.ok)throw new Error(d.error||'無法變更擁有者。');
        c.owner_id=id;c.owner_name=d.owner_name||person.name;c.owned=id===user.id;msg.textContent='已轉移。';
        const opt=sel.options[sel.selectedIndex];if(opt&&opt.value===c.course_id)opt.textContent=c.course_name+' · '+Number(c.unit_count||0)+' units'+(c.owned?'':' · '+c.owner_name)+' · '+c.course_id.slice(-8);
      }catch(e){pick.value=String(c.owner_id);msg.textContent=String(e&&e.message||e);}
      pick.disabled=false;});};
  render();if(sel)sel.addEventListener('change',render);
}

function whenBuilder(fn){
  let tries=0;const t=setInterval(()=>{const b=$('v241CourseBuilder');if(b){clearInterval(t);fn(b);}else if(++tries>150)clearInterval(t);},200);
}

async function init(){
  let r;
  try{r=await nativeFetch('/api/auth/me',{cache:'no-store'});}catch(_){return;}
  if(r.status===401){toLogin();return;}
  if(r.status===409){const d=await r.json().catch(()=>null);if(d&&d.code==='site_moved'&&/^https:\/\//.test(d.url||'')){location.replace(new URL(location.pathname+location.search,d.url).toString());return;}}
  if(!r.ok)return;
  const d=await r.json().catch(()=>null);if(!d||!d.user)return;
  user=d.user;window.VQ_AUTH_USER=user;
  const teacher=user.role==='teacher'||user.is_admin;
  document.body.classList.add(teacher?'vq-role-teacher':'vq-role-student');
  userBar();
  whenBuilder(b=>teacher?teacherBuilder(b):studentBuilder(b));
}
css();
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
