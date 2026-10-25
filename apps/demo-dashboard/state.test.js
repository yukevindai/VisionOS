import test from 'node:test';
import assert from 'node:assert/strict';
import {projectSnapshot, axisValue, stageStates} from './state.js';
const valid = {connected:true, fresh:true,validForMs:120,frameAgeMs:180,tracking:'LOCKED',axes:{x:{cm:-4.2}},guidance:'LEFT',distanceCm:12.4,frame:'data:image/jpeg;base64,AA'};
test('expired snapshot clears all physical outputs and camera',()=>{
 const s=projectSnapshot(valid,120);assert.equal(s.fresh,false);assert.equal(s.axes,null);assert.equal(s.guidance,null);assert.equal(s.distanceCm,null);assert.equal(s.frame,null);assert.equal(s.frameAgeMs,300);
});
test('round trip delay consumes remaining freshness',()=>{assert.equal(projectSnapshot(valid,130).fresh,false);assert.equal(projectSnapshot(valid,90).fresh,true);});
test('unlocking cannot retain physical guidance',()=>{const s=projectSnapshot({...valid,tracking:'HIDDEN'},0);assert.equal(s.axes,null);assert.equal(s.distanceCm,null);assert.equal(s.guidance,null);});
test('disconnected snapshot fails closed',()=>{assert.equal(projectSnapshot({...valid,connected:false}).frame,null);assert.equal(projectSnapshot({}).fresh,false);});
test('bad expiry or nonnumeric measurement never becomes a distance',()=>{assert.equal(projectSnapshot({...valid,validForMs:NaN}).fresh,false);assert.equal(axisValue({cm:NaN}),'—');assert.equal(axisValue({cm:-4.2}),'4.2 cm');});
test('pipeline stage labels are evidence based',()=>{assert.deepEqual(stageStates(projectSnapshot(valid,130)),['Waiting','Waiting','Not verified','Unavailable','Paused']);});


test('agent telemetry remains descriptive and uses reported handoffs', async () => {
  const {agentView} = await import('./state.js');
  const view = agentView({agent:'scene',status:'complete',goal:'describe',response:'A chair',latency_ms:123,
    events:[{source:'coordinator',destination:'scene',kind:'handoff'}]});
  assert.match(view.status, /scene: complete/);
  assert.equal(view.response, 'A chair');
  assert.deepEqual(view.events, ['coordinator -> scene']);
  assert.equal(agentView(null).status, 'Orchestration unavailable');
});


import {PreviewPresenter} from './preview.js';
const turn=()=>new Promise(resolve=>setImmediate(resolve));
test('slow preview survives guidance expiry, empty updates and decode failures',async()=>{
 const shown=[];
 const preview=new PreviewPresenter({decode:async src=>{if(src==='bad')throw Error('decode');return src;},commit:image=>shown.push(image)});
 preview.update('first');await turn();
 for(const elapsed of [300,1000,10000,60000]) {
  const state=projectSnapshot({...valid,previewFrame:'first'},elapsed);
  assert.equal(state.axes,null);assert.equal(state.guidance,null);
  preview.update(state.previewFrame);preview.update(null);
 }
 preview.update('bad');await turn();
 assert.equal(preview.hasFrame,true);assert.deepEqual(shown,['first']);
 preview.update('next');await turn();assert.deepEqual(shown,['first','next']);
});
test('decoding is bounded and keeps current image until replacement is ready',async()=>{
 const shown=[],resolvers=[];
 const preview=new PreviewPresenter({decode:src=>new Promise(resolve=>resolvers.push(()=>resolve(src))),commit:image=>shown.push(image)});
 preview.update('first');assert.equal(preview.hasFrame,false);
 resolvers.shift()();await turn();
 preview.update('second');preview.update('third');preview.update('latest');
 assert.deepEqual(shown,['first']);assert.equal(resolvers.length,1);
 resolvers.shift()();await turn();assert.deepEqual(shown,['first','second']);
 assert.equal(resolvers.length,1);resolvers.shift()();await turn();
 assert.deepEqual(shown,['first','second','latest']);
});

test('failed replacement cannot refresh age of the visible image',async()=>{
 const preview=new PreviewPresenter({decode:async src=>{if(src==='bad')throw Error();return src;},commit:()=>{}});
 preview.update('first',100,'SIMULATED');await turn();
 preview.update('bad',200,'LIVE');await turn();
 assert.equal(preview.receivedAt,100);assert.equal(preview.mode,'SIMULATED');
 preview.update('first',300,'SIMULATED');await turn();assert.equal(preview.receivedAt,300);
});
