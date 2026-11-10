require('../helpers.cjs');
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const sourceFile=process.argv[2]||'web/shared/js/speech/recognition-controller.js';
async function flush(){for(let i=0;i<24;i++)await Promise.resolve();}
function fixture({browserOnly=false,noPcm=false,worklet=false,sttFetch=null,deferSpeechStop=false,speech=false}={}){
  let now=10000,nextTimer=1;
  const timers=new Map(),elements=new Map(),recognizers=[],recorders=[],contexts=[],nodes=[],requests=[],submissions=[],modules=new Map();
  function schedule(fn,ms,repeat){const id=nextTimer++;timers.set(id,{fn,at:now+(ms||0),repeat});return id;}
  function tick(ms){const end=now+ms;let count=0;while(true){let id=null,t=null;for(const[k,v]of timers)if(v.at<=end&&(!t||v.at<t.at)){id=k;t=v;}if(!t)break;if(++count>10000)throw Error('Timer loop');now=t.at;if(t.repeat)t.at+=t.repeat;else timers.delete(id);t.fn();}now=end;}
  class El{
    constructor(){this.children=[];this.attrs={};this.style={};this.dataset={};this.listeners={};this._text='';this.scrollTop=0;this.clientHeight=200;this.disabled=false;this.classList={add(){},remove(){},contains(){return false;}};}
    set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
    set textContent(v){this._text=String(v);this.scrollTop=0;}get textContent(){return this._text;}
    get innerText(){return this.textContent;}get scrollHeight(){return Math.max(200,this._text.length*4);}
    setAttribute(k,v){this.attrs[k]=v;}appendChild(e){this.children.push(e);e.parentNode=this;return e;}
    insertBefore(e){return this.appendChild(e);}querySelector(){return null;}
    addEventListener(k,f){this.listeners[k]=f;}removeEventListener(k,f){if(this.listeners[k]===f)delete this.listeners[k];}
  }
  for(const id of ['studentQuestion','teacherAnswer','status','askBtn','stopAskBtn']){const e=new El();e.id=id;e.parentNode=new El();}
  const track={readyState:'live',stop(){this.readyState='ended';}},stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
  class SR{
    constructor(){recognizers.push(this);this.starts=0;this.listeners={};}
    addEventListener(k,f){(this.listeners[k]||=[]).push(f);}
    emit(k,data={}){for(const f of this.listeners[k]||[])f(data);if(this['on'+k])this['on'+k](data);}
    start(){this.starts++;this.connected=true;this.emit('start');}
    abort(){this.connected=false;this.emit('end');}
    stop(){this.connected=false;if(!deferSpeechStop)this.emit('end');}
    result(text,isFinal=true){const r=[{transcript:text}];r.isFinal=isFinal;this.emit('result',{resultIndex:0,results:[r]});}
    end(){this.connected=false;this.emit('end');}error(error){this.emit('error',{error});}
  }
  class Recorder{
    static isTypeSupported(){return true;}
    constructor(){this.state='inactive';this.mimeType='audio/webm';this.stopCalls=0;recorders.push(this);}
    start(){this.state='recording';if(this.onstart)this.onstart();}
    stop(){this.stopCalls++;this.state='inactive';if(this.finalChunk)this.chunk(this.finalChunk);this.stopped=Promise.resolve(this.onstop&&this.onstop());}
    chunk(text){if(this.ondataavailable)this.ondataavailable({data:new Blob([text])});}
  }
  class Node{
    constructor(){this.links=[];nodes.push(this);}connect(node){this.links.push(node);return node;}disconnect(){this.disconnected=true;}
  }
  class Context{
    constructor(){this.sampleRate=16000;this.state='running';this.destination=new Node();contexts.push(this);if(worklet)this.audioWorklet={addModule:async url=>{this.module=modules.get(url);}};}
    createMediaStreamSource(){return this.source=new Node();}
    createGain(){const n=new Node();n.gain={value:1};return this.gain=n;}
    createScriptProcessor(){const n=new Node();n.push=samples=>n.onaudioprocess&&n.onaudioprocess({inputBuffer:{getChannelData:()=>samples}});return this.processor=n;}
    resume(){this.state='running';return Promise.resolve();}close(){this.state='closed';return Promise.resolve();}
  }
  class WorkletNode extends Node{constructor(ctx){super();this.port={};this.push=samples=>this.port.onmessage&&this.port.onmessage({data:samples});ctx.processor=this;}}
  const document={readyState:'loading',getElementById:id=>elements.get(id)||null,createElement:()=>new El(),addEventListener(){},querySelector(){return null;},body:new El(),head:new El()};
  const synth={cancel(){},pause(){},resume(){},speak(){},getVoices(){return [];}};
  const window={speechSynthesis:synth,AbortController,requestAnimationFrame:fn=>schedule(fn,0),
    VQ_generateFinalAnswer:async q=>{submissions.push(q);return 'Test answer';},
    URL:{createObjectURL:b=>{const url='blob:mock-'+modules.size;modules.set(url,b);return url;},revokeObjectURL(){}}};
  if(browserOnly || speech)window.SpeechRecognition=SR;
  if(!browserOnly)window.MediaRecorder=Recorder;
  if(!noPcm)window.AudioContext=Context;
  if(worklet)window.AudioWorkletNode=WorkletNode;
  const sandbox={window,document,navigator:browserOnly?{}:{mediaDevices:{getUserMedia:async()=>stream}},
    localStorage:{getItem(){return null;},setItem(){}},Blob,Float32Array,ArrayBuffer,DataView,MediaRecorder:Recorder,console,
    SpeechSynthesisUtterance:class{constructor(t){this.text=t;}},Date:class extends Date{static now(){return now;}},performance:{now:()=>now},
    setTimeout:(f,ms)=>schedule(f,ms),clearTimeout:id=>timers.delete(id),setInterval:(f,ms)=>schedule(f,ms,ms),clearInterval:id=>timers.delete(id),
    fetch:async(url,options)=>{requests.push({url,options});return sttFetch?sttFetch(url,options):{ok:true,json:async()=>({text:options.headers['Content-Type']==='audio/wav'?'New spoken words.':'Full question including the last spoken sentence.',language:'en'})};}};
  // Include the legacy recognition wrapper: it must not disable manual Stop on end.
  vm.runInNewContext(fs.readFileSync(path.join(path.dirname(sourceFile),'stop-on-submit.js'),'utf8'),sandbox);
  let source=fs.readFileSync(sourceFile,'utf8');
  source=source.replace(/\}\)\(\);\s*$/,'window.__voiceTests={startRecognition,stopRecognitionAndSubmit,stopRecognitionForModeSwitch,submitQuestion,encodeVoiceWav,appendVoiceSegment};})();');
  vm.runInNewContext(source,sandbox,{filename:sourceFile});
  return {api:window.__voiceTests,window,document,recognizers,recorders,contexts,nodes,requests,submissions,elements,track,stream,tick,
    advance:async ms=>{tick(ms);await flush();},push:(seconds,value=.1)=>contexts.at(-1).processor.push(new Float32Array(Math.round(contexts.at(-1).sampleRate*seconds)).fill(value)),
    box:elements.get('studentQuestion'),stop:elements.get('stopAskBtn'),ask:elements.get('askBtn')};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
test('a long pause never stops audio or submits a question',async()=>{
  const f=fixture();await f.api.startRecognition();f.recorders[0].chunk('first');
  f.push(10,0);await f.advance(60000);f.push(4);await f.advance(1000);
  assert.equal(f.recorders[0].state,'recording');assert.equal(f.track.readyState,'live');assert.equal(f.recorders[0].stopCalls,0);
  assert.equal(f.submissions.length,0);assert.equal(f.stop.disabled,false);assert.equal(f.ask.disabled,true);
  assert.equal(f.recognizers.length,0);
});
test('successive audio windows keep appending text until explicit Stop',async()=>{
  const words=['First sentence.','Second sentence.','Third sentence.'];let i=0;
  const f=fixture({sttFetch:async()=>({ok:true,json:async()=>({text:words[i++]})})});await f.api.startRecognition();
  for(let n=0;n<3;n++){f.push(4);await f.advance(1000);}
  assert.equal(f.box.textContent,words.join(' '));assert.equal(f.submissions.length,0);assert.equal(f.recorders[0].state,'recording');
});
test('no counters, timestamps or extra status elements are created',async()=>{
  const f=fixture();await f.api.startRecognition();f.push(4);await f.advance(60000);
  assert.deepEqual([...f.elements.keys()],['studentQuestion','teacherAnswer','status','askBtn','stopAskBtn']);
});
test('manual Stop includes final recorded bytes and submits full STT exactly once',async()=>{
  const f=fixture();await f.api.startRecognition();const rec=f.recorders[0];rec.chunk('first');rec.chunk('middle');rec.finalChunk='last';
  f.api.stopRecognitionAndSubmit();f.api.stopRecognitionAndSubmit();await rec.stopped;await flush();
  assert.equal(rec.stopCalls,1);assert.equal(f.submissions.length,1);assert.match(f.submissions[0],/last spoken sentence/);
  const final=f.requests.find(r=>r.options.headers['Content-Type']!=='audio/wav');assert.equal(await final.options.body.text(),'firstmiddlelast');
  assert.equal(f.track.readyState,'ended');assert.equal(f.contexts[0].state,'closed');
});
test('unexpected recorder end waits for the student Stop before answering',async()=>{
  const f=fixture();await f.api.startRecognition();const rec=f.recorders[0];rec.chunk('recorded');rec.stop();await rec.stopped;
  assert.equal(f.requests.length,0);assert.equal(f.submissions.length,0);assert.equal(f.stop.disabled,false);
  f.api.stopRecognitionAndSubmit();await flush();assert.equal(f.submissions.length,1);
});
test('mode cancellation closes capture without submitting it',async()=>{
  const f=fixture();await f.api.startRecognition();const rec=f.recorders[0];rec.chunk('audio');f.api.stopRecognitionForModeSwitch();await rec.stopped;
  await f.advance(60000);assert.equal(f.submissions.length,0);assert.equal(f.requests.length,0);assert.equal(f.contexts[0].state,'closed');
});
test('a live STT response arriving after Stop cannot overwrite the final question',async()=>{
  let reply;const pending=new Promise(r=>{reply=r;});
  const f=fixture({sttFetch:async(url,o)=>o.headers['Content-Type']==='audio/wav'?pending:{ok:true,json:async()=>({text:'Final complete question'})}});
  await f.api.startRecognition();f.recorders[0].chunk('audio');f.push(4);await f.advance(1000);
  f.api.stopRecognitionAndSubmit();await f.recorders[0].stopped;
  reply({ok:true,json:async()=>({text:'Late preview'})});await flush();
  assert.equal(f.box.textContent,'Final complete question');assert.deepEqual(f.submissions,['Final complete question']);
});
test('temporary live STT failure neither stops recording nor answers early',async()=>{
  const f=fixture({sttFetch:async()=>{throw Error('offline');}});await f.api.startRecognition();f.push(4);await f.advance(1000);await f.advance(10000);
  assert.equal(f.recorders[0].state,'recording');assert.equal(f.submissions.length,0);assert.equal(f.requests.length,1);assert.equal(f.recognizers.length,0);
});
test('only one bounded PCM transcription window is in flight at a time',async()=>{
  let reply;const pending=new Promise(r=>{reply=r;});let first=true;
  const f=fixture({sttFetch:async()=>{if(first){first=false;return pending;}return {ok:true,json:async()=>({text:'Later speech'})};}});
  await f.api.startRecognition();f.push(4);await f.advance(1000);f.push(30);await f.advance(10000);assert.equal(f.requests.length,1);
  reply({ok:true,json:async()=>({text:'First speech'})});await flush();await f.advance(1000);
  assert.equal(f.requests.length,2);assert(f.requests[1].options.body.size<=44+Math.round(12.75*16000)*2);
});
test('PCM WAV encoding has valid headers, sample rate, size and clipping',async()=>{
  const f=fixture();const samples=new Float32Array([-2,-1,-.5,0,.5,1,2,NaN]);
  const blob=f.api.encodeVoiceWav(samples,48000),buffer=await blob.arrayBuffer(),v=new DataView(buffer),b=Buffer.from(buffer);
  assert.equal(b.toString('ascii',0,4),'RIFF');assert.equal(b.toString('ascii',8,12),'WAVE');assert.equal(v.getUint32(24,true),48000);
  assert.equal(v.getUint32(40,true),samples.length*2);assert.equal(v.getUint32(4,true),buffer.byteLength-8);
  assert.equal(v.getInt16(44,true),-32768);assert.equal(v.getInt16(54,true),32767);assert.equal(v.getInt16(58,true),0);
});
test('AudioWorklet capture stays connected through a muted output graph',async()=>{
  const f=fixture({worklet:true});await f.api.startRecognition();await flush();
  const ctx=f.contexts[0];assert.equal(ctx.gain.gain.value,0);assert.equal(ctx.source.links[0],ctx.processor);
  assert.equal(ctx.processor.links[0],ctx.gain);assert.equal(ctx.gain.links[0],ctx.destination);
  f.push(4);await f.advance(1000);assert.equal(f.requests.length,1);assert.equal(f.submissions.length,0);
  let Processor;const posted=[];
  vm.runInNewContext(await ctx.module.text(),{AudioWorkletProcessor:class{constructor(){this.port={postMessage:(frame,transfers)=>posted.push({frame,transfers})};}},Float32Array,registerProcessor:(name,p)=>{assert.equal(name,'vq-manual-voice');Processor=p;}});
  const p=new Processor();for(let n=0;n<32;n++)assert.equal(p.process([[new Float32Array(128).fill(.25)]]),true);
  assert.equal(posted.length,1);assert.equal(posted[0].frame.length,4096);assert.equal(posted[0].frame[0],.25);
});
test('text stays within the existing box and respects manual scrollback',async()=>{
  const long='Long spoken question. '.repeat(200);
  const f=fixture({sttFetch:async()=>({ok:true,json:async()=>({text:long})})});await f.api.startRecognition();
  f.push(4);await f.advance(1000);await f.advance(1);assert.equal(f.box.textContent,long.trim());assert.equal(f.box.scrollTop,f.box.scrollHeight);
  f.box.scrollTop=0;f.box.listeners.scroll();f.push(4);await f.advance(1000);await f.advance(1);assert.equal(f.box.scrollTop,0);
});
test('the browser-only fallback keeps listening across sessions until Stop, without auto-submit',async()=>{
  const f=fixture({browserOnly:true});await f.api.startRecognition();const sr=f.recognizers[0];sr.result('Captured words');
  await f.advance(60000);assert.equal(f.submissions.length,0);sr.end();await f.advance(1000);
  assert.equal(sr.starts,2);assert.equal(f.recognizers.length,1);assert.equal(f.stop.disabled,false);assert.equal(f.submissions.length,0);
  sr.result('and more words');assert.equal(f.box.textContent,'Captured words and more words');
  f.api.stopRecognitionAndSubmit();await flush();await f.advance(2000);assert.deepEqual(f.submissions,['Captured words and more words']);
});
test('the browser-only fallback stops restarting after a fatal recognition error',async()=>{
  const f=fixture({browserOnly:true});await f.api.startRecognition();const sr=f.recognizers[0];sr.result('Kept text');
  sr.error('not-allowed');sr.end();await f.advance(5000);assert.equal(sr.starts,1);assert.equal(f.stop.disabled,false);
  f.api.stopRecognitionAndSubmit();await flush();await f.advance(2000);assert.deepEqual(f.submissions,['Kept text']);
});
test('browser-only manual Stop waits for final recognition results',async()=>{
  const f=fixture({browserOnly:true,deferSpeechStop:true});await f.api.startRecognition();const sr=f.recognizers[0];sr.result('First part');
  f.api.stopRecognitionAndSubmit();assert.equal(f.submissions.length,0);sr.result('First part and final words');sr.end();await flush();await f.advance(2000);
  assert.deepEqual(f.submissions,['First part and final words']);
});
test('other submission calls cannot answer while voice recording is active',async()=>{
  const f=fixture();await f.api.startRecognition();f.box.textContent='An unfinished question';await f.api.submitQuestion();
  assert.equal(f.submissions.length,0);assert.equal(f.recorders[0].state,'recording');
});
test('a stale full-audio transcription cannot overwrite a new mode',async()=>{
  let reply;const pending=new Promise(r=>{reply=r;});const f=fixture({sttFetch:()=>pending});await f.api.startRecognition();f.recorders[0].chunk('audio');
  f.api.stopRecognitionAndSubmit();f.api.stopRecognitionForModeSwitch();f.box.textContent='New question';
  reply({ok:true,json:async()=>({text:'Old question'})});await f.recorders[0].stopped;assert.equal(f.box.textContent,'New question');assert.equal(f.submissions.length,0);
});
test('missing live text and failed final STT never submit a listening placeholder',async()=>{
  const f=fixture({noPcm:true,sttFetch:async()=>{throw Error('offline');}});await f.api.startRecognition();f.recorders[0].chunk('audio');
  f.api.stopRecognitionAndSubmit();await f.recorders[0].stopped;assert.equal(f.submissions.length,0);
});
test('overlapping audio windows do not duplicate their matching boundary text',async()=>{
  const f=fixture();assert.equal(f.api.appendVoiceSegment('肺泡通氣與灌流','與灌流的關係'),'肺泡通氣與灌流的關係');
  assert.equal(f.api.appendVoiceSegment('the pulmonary blood flow','blood flow changes'),'the pulmonary blood flow changes');
});
test('R9: STT uses the working /api/kb/stt route and falls back when a route is missing',async()=>{
  const urls=[];const f=fixture({noPcm:true,sttFetch:async(url)=>{urls.push(url);return url==='/api/kb/stt'?{ok:false,status:404}:{ok:true,status:200,json:async()=>({text:'Routed question'})};}});
  await f.api.startRecognition();f.recorders[0].chunk('audio');f.api.stopRecognitionAndSubmit();await f.recorders[0].stopped;await flush();
  assert.deepEqual(urls,['/api/kb/stt','/api/reference/stt']);assert.deepEqual(f.submissions,['Routed question']);
  const g=fixture({noPcm:true});await g.api.startRecognition();g.recorders[0].chunk('audio');g.api.stopRecognitionAndSubmit();await g.recorders[0].stopped;await flush();
  assert.equal(g.requests[0].url,'/api/kb/stt');assert.equal(g.submissions.length,1);
});
test('R9: browser live text appears while recording and never submits before Stop',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();const sr=f.recognizers[0];
  assert.equal(sr.continuous,true);assert.equal(sr.interimResults,true);
  sr.result('肺泡通氣',false);assert.equal(f.box.textContent,'肺泡通氣');
  sr.result('肺泡通氣量如何影響',false);assert.equal(f.box.textContent,'肺泡通氣量如何影響');
  f.push(10);await f.advance(30000);
  assert.equal(f.requests.length,0,'server STT stays idle while browser live text works');
  assert.equal(f.submissions.length,0);assert.equal(f.recorders[0].state,'recording');assert.equal(f.stop.disabled,false);assert.equal(f.ask.disabled,true);
});
test('R9: browser live text restarts after the browser ends a session, keeping earlier text',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();
  f.recognizers[0].result('First part');await f.advance(5000);f.recognizers[0].end();
  assert.equal(f.stop.disabled,false,'session end must not reset Stop');await f.advance(1000);
  assert.equal(f.recognizers.length,2);f.recognizers[1].result('second part',false);
  assert.equal(f.box.textContent,'First part second part');assert.equal(f.submissions.length,0);
});
test('R9: final Faster-Whisper text is authoritative over browser live text',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();f.recognizers[0].result('rough live words');
  const rec=f.recorders[0];rec.chunk('a');rec.finalChunk='b';f.api.stopRecognitionAndSubmit();await rec.stopped;await flush();
  assert.equal(rec.stopCalls,1);assert.deepEqual(f.submissions,['Full question including the last spoken sentence.']);
  assert.equal(f.recognizers[0].connected,false);
});
test('R9: final STT failure submits the browser live text once',async()=>{
  const f=fixture({speech:true,noPcm:true,sttFetch:async()=>{throw Error('offline');}});await f.api.startRecognition();
  f.recognizers[0].result('Live question text');const rec=f.recorders[0];rec.chunk('a');f.api.stopRecognitionAndSubmit();await rec.stopped;await flush();
  assert.deepEqual(f.submissions,['Live question text']);
});
test('R9: a fatal browser recognition error switches live text to server Whisper windows',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();const sr=f.recognizers[0];
  sr.error('network');sr.end();await f.advance(1000);assert.equal(f.recognizers.length,1);
  f.push(4);await f.advance(1000);assert.equal(f.requests.length,1);assert.equal(f.requests[0].url,'/api/kb/stt');
  assert.equal(f.box.textContent,'New spoken words.');assert.equal(f.submissions.length,0);
});
test('R9: mode switch closes browser live text without restarting or submitting',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();f.recognizers[0].result('Unsent');
  f.api.stopRecognitionForModeSwitch();await f.advance(5000);assert.equal(f.recognizers.length,1);assert.equal(f.submissions.length,0);
});
test('R9: when browser recognition stays silent, server Whisper windows still show live text',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();assert.equal(f.recognizers.length,1);
  f.push(4);await f.advance(1000);assert.equal(f.requests.length,1);assert.equal(f.box.textContent,'New spoken words.');
  // Browser words continue after the Whisper words instead of erasing them.
  f.recognizers[0].result('Browser words now');assert.equal(f.box.textContent,'New spoken words. Browser words now');
  f.push(8);await f.advance(5000);assert.equal(f.requests.length,1,'Whisper preview pauses once browser text works');
  assert.equal(f.submissions.length,0);
});
test('R19: a browser recognizer that stalls mid-recording is restarted and Whisper fills the gap',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();
  f.recognizers[0].result('First words');assert.equal(f.box.textContent,'First words');
  // The student keeps talking, but the browser recognizer goes quiet without ending.
  await f.advance(6000);f.push(3);await f.advance(1000);
  assert.equal(f.requests.length,1,'Whisper transcribes what the browser missed');
  assert.equal(f.box.textContent,'First words New spoken words.');
  await f.advance(1000);assert.equal(f.recognizers.length,2,'a new browser recognizer is started');
  f.recognizers[1].result('and the rest',false);
  assert.equal(f.box.textContent,'First words New spoken words. and the rest');
  f.recognizers[1].result('and the rest of the question');
  f.push(4);await f.advance(3000);assert.equal(f.requests.length,1,'Whisper pauses again once browser text resumes');
  assert.equal(f.box.textContent,'First words New spoken words. and the rest of the question');
  assert.equal(f.submissions.length,0);assert.equal(f.recorders[0].state,'recording');assert.equal(f.stop.disabled,false);
});
test('R19: a silent pause is not a stall: no restart and no server transcription',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();f.recognizers[0].result('Question so far');
  f.push(20,0);await f.advance(30000);
  assert.equal(f.recognizers.length,1);assert.equal(f.requests.length,0);assert.equal(f.box.textContent,'Question so far');
});
test('R19: text keeps growing through a long recording until Stop Recording & Answer',async()=>{
  const f=fixture({speech:true});await f.api.startRecognition();
  const expected=[];
  for(let n=0;n<6;n++){
    const sr=f.recognizers.at(-1);sr.result('part '+n);expected.push('part '+n);
    if(n%2){sr.end();await f.advance(1000);}            // the browser ends a session
    else{await f.advance(7000);f.push(2);await f.advance(2000);expected.push('New spoken words.');}  // or stalls
    await f.advance(1000);
  }
  assert.equal(f.box.textContent,expected.join(' '));
  assert.equal(f.submissions.length,0);assert.equal(f.recorders[0].state,'recording');
  const rec=f.recorders[0];rec.chunk('a');f.api.stopRecognitionAndSubmit();await rec.stopped;await flush();
  assert.deepEqual(f.submissions,['Full question including the last spoken sentence.']);
});
test('R11: a down KB backend (HTTP 502) falls through to /api/reference/stt',async()=>{
  const urls=[];const f=fixture({noPcm:true,sttFetch:async(url)=>{urls.push(url);return url==='/api/kb/stt'?{ok:false,status:502}:{ok:true,status:200,json:async()=>({text:'Rerouted question'})};}});
  await f.api.startRecognition();f.recorders[0].chunk('audio');f.api.stopRecognitionAndSubmit();await f.recorders[0].stopped;await flush();
  assert.deepEqual(urls,['/api/kb/stt','/api/reference/stt']);assert.deepEqual(f.submissions,['Rerouted question']);
});
(async()=>{let failed=0;for(const[name,fn]of tests)try{await fn();console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+'\n'+e.stack);}
console.log(`${tests.length-failed}/${tests.length} manual-stop voice mock checks passed. No microphone, real STT/model, SSH or public deployment used.`);process.exitCode=failed?1:0;})();
