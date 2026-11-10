/* Exercise the actual rendering functions with image work still running. */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../../app/web/shared/js/course/course-controller.js'),'utf8');
const functions=source.slice(source.indexOf('function prepRow('),source.indexOf('async function retryPreparation('));
const box={hidden:true,innerHTML:''};
const context={manifest:{course_name:'Fixture',units:[{question_count:2,script_status:'ready'}],
  preparation:{questions:{status:'ready',ready_units:1},lectures:{status:'ready',completed_units:1},
    index:{status:'ready'},figures:{status:'preparing',total:100,completed:3}}},
  activity:null,$:()=>box,esc:v=>String(v)};
vm.createContext(context);vm.runInContext(functions+';renderPrep("")',context);
assert(!box.innerHTML.includes('Slide images'));
assert(box.innerHTML.includes('Practice questions')&&box.innerHTML.includes('Chinese lecture scripts')&&box.innerHTML.includes('Semantic search'));
assert(box.innerHTML.includes('>Ready</span>'),'hidden optional image work must not hold the main readiness label');
context.manifest.ingestion_complete=false;
vm.runInContext('renderPrep("")',context);
assert(box.innerHTML.includes('>Preparing</span>'),'partial ingestion must not claim the full course is ready');
console.log('PASS image progress row removed; main preparation and partial-ingestion readiness remain correct');
