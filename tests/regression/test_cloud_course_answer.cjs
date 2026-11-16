'use strict';
// Uploaded-course answers from a cloud model follow the default mode: answered from the course material,
// then at most one lenient second look that keeps a correct answer and fixes a wrong one. No quote has to be
// found in the PDF text, so an ordinary question about the course is never refused by the check.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const worker=fs.readFileSync('cloudflare/worker/src/index.js','utf8');
const grab=name=>{
  const start=Math.max(worker.indexOf('\nfunction '+name+'('),worker.indexOf('\nasync function '+name+'('));
  assert(start>=0,name);
  const next=worker.slice(start+1).search(/\n(?:async )?function |\nconst [A-Z_]+=/);
  return worker.slice(start,next<0?undefined:start+1+next);
};
const calls=[];let script=[];
const context={console,calls,
  responseContract:()=>({text:'format'}),languageInstruction:()=>'Answer in Traditional Chinese.',
  callProviderRaw:async(cloud,system,user)=>{calls.push({system,user});const next=script.shift();if(next instanceof Error)throw next;return next;}};
vm.createContext(context);
vm.runInContext(['cleanCourseAnswer','looseJsonObject','authLike','answerOptionLetter','detectResponseTask','parseMcqOptions','looksIncompleteAnswer','courseGroundedSystem','courseProviderAnswer','reviewCourseAnswer'].map(grab).join('\n'),context);
const ask=(question,source)=>context.courseProviderAnswer({provider:'openai'},question,source||'Machine learning studies algorithms that improve with data.','zh-TW','Machine Learning');
const mcq='Which is supervised learning?\nA. Clustering\nB. Regression\nC. Dimensionality reduction\nD. Reinforcement';

(async()=>{
  // A plain question is answered by one model call, whatever the wording of the course text: nothing is refused.
  script=['問題：甚麼是 machine learning\n回答：機器學習是讓電腦從資料中學習、不用逐步寫規則的方法。'];
  const plain=await ask('甚麼是machine learning');
  assert(plain.includes('機器學習')&&calls.length===1,'knowledge questions need no second call');
  assert(!/教材不足|course_grounding/.test(plain));

  // Multiple choice and calculations are looked at again; a correct answer is kept as it is.
  calls.length=0;
  script=['正確答案：B. Regression\n解釋：迴歸使用有標籤的資料。',JSON.stringify({verdict:'pass',corrected_answer:''})];
  assert.equal(await ask(mcq),'正確答案：B. Regression\n解釋：迴歸使用有標籤的資料。');
  assert.equal(calls.length,2);
  assert(/Review trigger:\nmultiple_choice_conceptual/.test(calls[1].user));

  // A wrong answer is replaced by the reviewer's minimal correction.
  calls.length=0;
  script=['正確答案：A. Clustering\n解釋：分群。',JSON.stringify({verdict:'correct',corrected_answer:'正確答案：B. Regression\n解釋：迴歸使用有標籤的資料。'})];
  assert.equal(await ask(mcq),'正確答案：B. Regression\n解釋：迴歸使用有標籤的資料。');

  // A review that cannot be read keeps the answer, as in the default mode; so does a failed review call.
  for(const outcome of ['Sure, looks fine!',new Error('timeout'),'{"verdict":"correct"}']){
    script=['正確答案：B. Regression\n解釋：有標籤。',outcome];
    assert.equal(await ask(mcq),'正確答案：B. Regression\n解釋：有標籤。');
  }
  // ...but a bad API key is still reported.
  script=['正確答案：B. Regression\n解釋：有標籤。',Object.assign(new Error('Unauthorized'),{status:401})];
  await assert.rejects(()=>ask(mcq));

  // An answer that stops in the middle is looked at again even for a plain question.
  calls.length=0;
  script=['回答：機器學習是讓電腦從資料中學習，因此',JSON.stringify({verdict:'correct',corrected_answer:'回答：機器學習是讓電腦從資料中學習。'})];
  assert.equal(await ask('甚麼是machine learning'),'回答：機器學習是讓電腦從資料中學習。');

  // A Chinese question answered in English is rewritten once in Chinese (as in the default mode); the prompt carries its style rules.
  calls.length=0;
  const chinese='機器學習是讓電腦從資料中自行找出規律的方法，不需要人工逐條撰寫規則，也能隨著資料增加而改善表現。';
  script=['Machine learning lets computers find patterns in data without hand-written rules and improve as data grows.',chinese];
  assert.equal(await ask('甚麼是machine learning'),chinese);
  assert.equal(calls.length,2);
  assert(/Rewrite the supplied draft only/.test(calls[1].user)&&calls[1].user.includes('Machine learning lets computers'));
  assert(/根據/.test(calls[0].system)&&/restate the exact question/.test(calls[0].system),'same style rules as the default mode');
  // A rewrite that fails keeps the first answer.
  script=['Machine learning lets computers find patterns in data without hand-written rules and improve as data grows.',new Error('timeout')];
  assert(/^Machine learning lets/.test(await ask('甚麼是machine learning')));

  // The route no longer has a quote gate or the physiology sanitizer.
  const route=worker.slice(worker.indexOf('async function courseAnswer('),worker.indexOf('\nasync function proxy('));
  assert(!/course_grounding_rejected|validateCourseReview/.test(worker),'no quote gate');
  assert(!route.includes('cleanStudentAnswer('));
  console.log('[OK] cloud course answers follow the default mode: plain questions are answered at once, multiple-choice and calculations get one lenient review (kept when correct, corrected when wrong, draft kept when the review is unusable), cut-off answers are reviewed, no quote has to match the PDF text');
})().catch(e=>{console.error(e);process.exit(1);});
