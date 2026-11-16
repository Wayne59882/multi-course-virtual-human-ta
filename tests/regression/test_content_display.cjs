'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
for(const site of ['reference','kb']){
  const context={window:{}};vm.runInNewContext(fs.readFileSync(`web/shared/js/course/course-content.js`,'utf8'),context);
  const content=context.window.CourseContent;
  const samples=['CO₂ + H₂O → glucose; Ca²⁺; pH 7.40.','5 µg/mL; 25 °C; α + β; Na⁺ + Cl⁻.','V_A = 12 V; R = 6 Ω; E = mc².','Table 2 shows GDP = 10 million USD. Price $20; price $40.','中文課程：細胞膜維持離子濃度差。','Treatment | Dose\nDrug A | 5 µg/mL\nDrug B | 10 µg/mL','PHP example: `$x=1;`'];
  for(const text of samples)assert.equal(content.clean(text),text);
  for(const text of ['cafÃ©','25 Â°C','CO\uFFFD','private \uE010']){assert.throws(()=>content.clean(text));assert(!content.display(text).includes(text));}
  const worker=fs.readFileSync('cloudflare/worker/src/index.js','utf8');
  vm.runInNewContext(worker.slice(worker.indexOf('function cleanCourseAnswer('),worker.indexOf('function cleanStudentAnswer('))+'\nthis.cleanCloud=cleanCourseAnswer;',context);
  for(const text of samples)assert.equal(context.cleanCloud(text),text);
  const courseBranch=worker.slice(worker.indexOf('async function courseAnswer('),worker.indexOf('\nasync function proxy('));
  assert(!courseBranch.includes('cleanStudentAnswer('),'uploaded courses must bypass the physiology sanitizer');
  const lecture=fs.readFileSync(`web/shared/js/speech/recognition-controller.js`,'utf8');
  context.window.VQ_UPLOADED_COURSE_ACTIVE=true;
  vm.runInNewContext(lecture.slice(lecture.indexOf('  function plainStudentAnswer('),lecture.indexOf('  function setStatus('))+'\nthis.display=plainStudentAnswer;',context);
  for(const text of samples)assert.equal(context.display(text),text);
  class Synth{speak(){}cancel(){}pause(){}resume(){}getVoices(){return []}}
  class Utterance{constructor(text){this.text=text}}
  const speechWindow={VQ_UPLOADED_COURSE_ACTIVE:true,speechSynthesis:new Synth(),SpeechSynthesis:Synth};
  const speech={window:speechWindow,SpeechSynthesisUtterance:Utterance,document:{readyState:'loading',getElementById(){return null},querySelector(){return null},addEventListener(){}},setTimeout(){},clearTimeout(){},console};
  vm.runInNewContext(fs.readFileSync(`web/shared/js/speech/voice-controller.js`,'utf8'),speech);
  assert.equal(speechWindow.VQ_medicalPronunciation('V_A = 12 V; V_T is a physics variable.','zh-TW'),'V_A = 12 V; V_T is a physics variable.');
  // Currency and table text must not be mistaken for an inline formula.
  vm.runInNewContext(fs.readFileSync(`web/shared/vendor/temml/temml.min.js`,'utf8'),context);context.window.temml=context.temml;
  vm.runInNewContext(fs.readFileSync(`web/shared/js/course/course-math.js`,'utf8'),context);
  const price=context.window.CourseMath.html('Price $20 per unit; price $40 per unit.');
  assert(!price.includes('<math'));assert(price.includes('$20')&&price.includes('$40'));
  assert(context.window.CourseMath.html(String.raw`$u_{tt}-\nabla^2u=0$`).includes('<math'));
  console.log(`[OK] ${site}: all-subject Unicode/units, local/cloud display, TTS variables, damaged-text rejection, currency vs equations`);
}
