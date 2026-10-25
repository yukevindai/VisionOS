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
