/* The teacher's arrangement of units is applied to the course the page shows; the stored list is only a fallback. */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../../app/web/shared/js/course/course-controller.js'),'utf8');
const helper=source.slice(source.indexOf('function orderedManifest('),source.indexOf('function setBuildStatus('));
const context={};vm.createContext(context);vm.runInContext(helper,context);
const ids=m=>m.units.map(u=>u.id).join(',');
const make=extra=>Object.assign({units:[{id:'a'},{id:'b'},{id:'c'},{id:'d'}]},extra);
assert.strictEqual(ids(context.orderedManifest(make({unit_order:['c','a','b','d']}))),'c,a,b,d','the arranged order is shown');
assert.strictEqual(ids(context.orderedManifest(make({unit_order:['d','b']}))),'d,b,a,c','units missing from the arrangement follow, in their stored order');
assert.strictEqual(ids(context.orderedManifest(make({}))),'a,b,c,d','no arrangement: stored order');
assert.strictEqual(ids(context.orderedManifest(make({unit_order:[]}))),'a,b,c,d');
assert.strictEqual(context.orderedManifest(null),null);
assert(source.includes('/api/reference/course-edit-units')&&source.includes('v241EditUnits'),'the teacher panel saves through the edit-units route');
console.log('PASS arranged unit order is applied to the course the page shows');
