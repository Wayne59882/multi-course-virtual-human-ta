require('../helpers.cjs');
'use strict';
// Site address: the thesis-named Worker passes requests to the teaching Worker,
// and the old workers.dev address forwards visitors once SITE_URL is set.
const assert=require('node:assert/strict');
const fs=require('node:fs');
async function load(file){return (await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(file,'utf8')).toString('base64'))).default;}
async function main(){
  const proxy=await load('cloudflare/worker/src/site-proxy.js'),app=await load('cloudflare/worker/src/index.js');
  const seen=[];const binding={fetch:async request=>{seen.push(request.url);return new Response('app '+new URL(request.url).pathname);}};
  const viaProxy=await proxy.fetch(new Request('https://multicourse-ta.twchu.workers.dev/login/?next=%2Freference%2F'),{APP:binding});
  assert.equal(await viaProxy.text(),'app /login/');assert.deepEqual(seen,['https://multicourse-ta.twchu.workers.dev/login/?next=%2Freference%2F']);
  const assets={fetch:async request=>new Response('asset '+new URL(request.url).pathname)};
  const env={SITE_URL:'https://multicourse-ta.twchu.workers.dev',ORIGIN_URL:'https://origin.test',ASSETS:assets};
  const old=await app.fetch(new Request('https://lungta.twchu.workers.dev/reference/?x=1'),env);
  assert.equal(old.status,302);assert.equal(old.headers.get('location'),'https://multicourse-ta.twchu.workers.dev/reference/?x=1');
  // A page left open on the old address calls the API with fetch, which cannot follow a redirect
  // to another site: it gets the new address as JSON and the page moves itself.
  const api=await app.fetch(new Request('https://lungta.twchu.workers.dev/api/auth/login',{method:'POST',body:'{}'}),env);
  assert.equal(api.status,409);const moved=await api.json();
  assert.equal(moved.code,'site_moved');assert.equal(moved.url,'https://multicourse-ta.twchu.workers.dev/login/');
  assert.match(moved.error,/multicourse-ta\.twchu\.workers\.dev\/login\//);
  const login=fs.readFileSync('web/login/index.html','utf8'),roles=fs.readFileSync('web/shared/js/auth/role-access.js','utf8');
  assert.ok(login.includes("d.code==='site_moved'")&&login.includes('moved(d);')&&login.includes('r.status===409&&moved('));
  assert.ok(roles.split("d.code==='site_moved'").length===3,'role-access handles the move on load and on later API calls');
  // Requests arriving under the new address (through the binding) are served normally.
  assert.equal(await (await app.fetch(new Request('https://multicourse-ta.twchu.workers.dev/login/'),env)).text(),'asset /login/');
  // Before the move (no SITE_URL) and on local test hosts nothing is redirected.
  assert.equal(await (await app.fetch(new Request('https://lungta.twchu.workers.dev/login/'),{ORIGIN_URL:'https://origin.test',ASSETS:assets})).text(),'asset /login/');
  assert.equal(await (await app.fetch(new Request('http://127.0.0.1:8787/login/'),env)).text(),'asset /login/');
  console.log('[OK] site address: thesis-named Worker passes requests through; old address forwards pages and tells open pages where the site moved, only after the move');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
