import {PreviewPresenter} from './preview.js';
import {projectSnapshot, axisValue, stageStates, agentView} from './state.js';
const $ = id => document.getElementById(id);
const preview = new URLSearchParams(location.search).get('preview');
const previewMode = ['active','scan','lost'].includes(preview);
let sampleState = preview, token = null, snapshot = null, capturedAt = 0, roundTrip = 0, revision = 0, busy = false, stopped = false;
let lastAnnouncement = '', lastDiagnostics = '';
const cameraPreview = new PreviewPresenter({
 decode: async src => {const image=new Image();image.src=src;await image.decode();return image;},
 commit: image => {image.id='camera';image.alt='Camera preview without guidance overlays';$('camera').replaceWith(image);$('camera-empty').hidden=true;}
});
const buttons = [...document.querySelectorAll('[data-command]')];
function put(id,value) {const text=String(value ?? ''); if($(id).textContent!==text) $(id).textContent=text;}
function notify(message) {put('notice',message);$('notice').hidden=!message;}
function clearEvidence(message) {snapshot=null;render();if(message)notify(message);}
function render() {
 const elapsed = Math.max(0,performance.now()-capturedAt)+roundTrip;
 const s=projectSnapshot(snapshot,elapsed), online=s.connected===true;
 const simulated=previewMode||s.mode==='SIMULATED';
 put('connection',previewMode?'SIMULATED DESIGN PREVIEW':online?(simulated?'SIMULATED PIPELINE':'LOCAL PIPELINE CONNECTED'):'DISCONNECTED');
 document.body.classList.toggle('is-stale',!s.fresh);
 buttons.forEach(b=>b.disabled=previewMode||!token||(b.id!=='stop'&&(!online||busy))||(b.id==='quick'&&!s.quickAvailable)||(b.id==='recall'&&!s.orchestration));
 const phase=!online?'WAITING FOR CONNECTION':s.axes?'GUIDANCE AVAILABLE':s.fresh&&s.tracking==='SEARCHING'?'SCANNING SCENE':'GUIDANCE PAUSED';
 put('phase',phase);
 const instruction=s.guidance ? String(s.guidance).replaceAll('_',' ') : !online?'Ready when you are':s.tracking==='SEARCHING'?'Finding your object':!s.fresh?'Tracking paused':s.tracking==='LOCKED'?'Waiting for valid depth':'Target lost';
 put('instruction',instruction);
 put('target',s.fresh&&s.target?`${s.target} · ${s.tracking==='LOCKED'?'selected target':'searching'}`:online?'Keep your hand and target in view.':'Choose an object to begin.');
 put('distance',s.distanceCm===null?'—':`${s.distanceCm.toFixed(1)} cm`);
 put('distance-note',s.distanceCm===null?'Awaiting valid depth':'Measured depth');
 put('frame-age',s.frameAgeMs===null?'—':`${Math.round(s.frameAgeMs)} ms`);
 put('fps',Number.isFinite(s.fps)?`${s.fps.toFixed(1)} FPS`:'No telemetry');put('depth',s.depth);
 for(const [axis,fallback] of [['x','LEFT / RIGHT'],['y','UP / DOWN'],['z','FORWARD / BACK']]){const a=s.axes?.[axis];put(`${axis}-label`,a?.direction||fallback);put(`${axis}-value`,axisValue(a));}
 cameraPreview.update(s.previewFrame,Number.isFinite(s.previewAgeMs)?performance.now()-s.previewAgeMs-elapsed:null,s.previewMode);
 const showFrame=cameraPreview.hasFrame;
 $('camera-empty').hidden=showFrame;
 const age=Number.isFinite(cameraPreview.receivedAt)?Math.max(0,performance.now()-cameraPreview.receivedAt):null;
 put('camera-label',!showFrame?'WAITING FOR CAMERA':!online?`${cameraPreview.mode==='SIMULATED'?'SIMULATED | ':''}DISCONNECTED | LAST CAMERA IMAGE`:
   (previewMode||cameraPreview.mode==='SIMULATED')?'SIMULATED CAMERA PREVIEW':s.previewAvailable===false?'CAMERA UNAVAILABLE | LAST CAMERA IMAGE':age===null?'LAST CAMERA IMAGE':
   `CAMERA PREVIEW | ${(age/1000).toFixed(1)}s old${age>=300?' | awaiting next frame':''}`);
 put('audio-cue',s.audio?.lastCue?String(s.audio.lastCue).replaceAll('_',' '):'No cue reported');
 put('audio-status',`Laptop audio: ${s.audio?.enabled?'enabled':'off / unavailable'}${s.audio?.playback?` · ${s.audio.playback}`:''}`);
 put('voice-status',`Microphone: ${s.voice?.inputDevice||'not selected'} · ${s.voice?.status||'awaiting status'}`);
 put('voice-transcript',s.voice?.transcript ? `“${s.voice.transcript}”` : 'No speech recognized yet.');
 put('voice-recognition',s.voice?.recognition||'');
 put('voice-submitted',s.voice?.submittedPrompt ? `Last sent to agent: ${s.voice.submittedPrompt}` : '');
 put('reason',!s.fresh?'Current evidence unavailable. Physical directions are cleared.':s.audio?.suppressed||s.detail||'Fresh tracking and calibrated depth are required for physical guidance.');
 const agents=agentView(s.orchestration,online);
 put('agent-status',agents.status);put('agent-goal',agents.goal);put('agent-response',agents.response);
 const eventText=agents.events.join('\n');
 if($('agent-events').dataset.value!==eventText){$('agent-events').replaceChildren(...agents.events.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));$('agent-events').dataset.value=eventText;}
 const steps=stageStates(s);['scene','object','hand','depth','guidance'].forEach((key,i)=>put(`step-${key}`,steps[i]));
 put('mode-note',simulated?'SIMULATED · not a live hardware session':online?'Live pipeline · hardware telemetry unavailable':'Waiting for local tracking process');
 if(!$('diagnostics').hidden){const diagnostic={...s,frame:s.frame?'[current image omitted]':null,previewFrame:s.previewFrame?'[preview image omitted]':null};const formatted=JSON.stringify(diagnostic,null,2);if(formatted!==lastDiagnostics){put('diagnostic-data',formatted);lastDiagnostics=formatted;}}
 if(phase!==lastAnnouncement){$('instruction').setAttribute('aria-label',`${phase}: ${instruction}`);lastAnnouncement=phase;}
}
async function poll() {
 if(stopped||previewMode)return;
 const rev=revision, start=performance.now();
 try {
  if(!token){const r=await fetch('/api/session',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw new Error('Dashboard connection unavailable');token=(await r.json()).token;}
  const r=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw new Error('Tracking process unavailable');
  const data=await r.json();if(rev!==revision||document.hidden)return;
  snapshot=data;capturedAt=performance.now();roundTrip=capturedAt-start;render();
 }catch(error){if(rev===revision){token=null;clearEvidence('Connection lost. Start the tracking process or reconnect.');}}
 finally{if(!stopped)setTimeout(poll,100);}
}
async function command(action) {
 if(previewMode||!token)return;
 revision++;busy=true;clearEvidence();notify(action==='stop'?'Sending Stop to the tracking process…':'Sending request…');
 try{const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json','X-Reflex-Token':token},body:JSON.stringify({action,query:$('query').value.trim()}),signal:AbortSignal.timeout(2000)});if(!r.ok)throw new Error(`Request rejected (${r.status})`);notify(action==='stop'?'Stop request accepted by the tracking process.': 'Request accepted. Waiting for current evidence.');}
 catch(e){notify(`${e.message}. Use the existing desktop controls if the bridge is unavailable.`);}
 finally{busy=false;render();}
}
$('controls').addEventListener('submit',e=>{e.preventDefault();if($('query').value.trim())void command('request');});
$('stop').addEventListener('click',()=>void command('stop'));
$('describe').addEventListener('click',()=>void command('describe'));
$('recall').addEventListener('click',()=>void command('recall'));
$('quick').addEventListener('click',()=>void command('quick'));
$('setup-open').addEventListener('click',()=>$('setup').showModal());
$('setup-close').addEventListener('click',()=>$('setup').close());
$('reconnect').addEventListener('click',()=>{revision++;token=null;clearEvidence();notify(previewMode?'This preview is simulated and does not connect to hardware.':'Reconnecting to local tracking…');$('setup').close();});
$('diagnostics-toggle').addEventListener('click',()=>{$('diagnostics').hidden=!$('diagnostics').hidden;$('diagnostics-toggle').setAttribute('aria-expanded',String(!$('diagnostics').hidden));render();});
$('presentation').addEventListener('click',()=>{const on=document.body.classList.toggle('presentation');$('presentation').setAttribute('aria-pressed',String(on));put('presentation',on?'Exit presentation':'Presentation view');});

document.addEventListener('visibilitychange',()=>{revision++;clearEvidence();});
window.addEventListener('pagehide',()=>{stopped=true;clearEvidence();});
if(previewMode){
 const label=document.createElement('label');label.textContent='Preview state ';const select=document.createElement('select');select.setAttribute('aria-label','Preview state');for(const state of ['active','scan','lost']){const o=document.createElement('option');o.value=state;o.textContent=state;select.append(o);}select.value=sampleState;select.addEventListener('change',()=>sampleState=select.value);label.append(select);$('setup').append(label);
 const sample=()=>{const active=sampleState==='active',fresh=sampleState!=='lost';snapshot={connected:true,mode:'SIMULATED',tracking:active?'LOCKED':sampleState==='scan'?'SEARCHING':'HIDDEN',fresh,validForMs:fresh?206:0,frameAgeMs:fresh?94:450,fps:5.1,target:'Pepsi can',previewFrame:'preview-scene.svg',previewAgeMs:94,frame:'preview-scene.svg',distanceCm:active?12.4:null,axes:active?{x:{direction:'LEFT',cm:4.2},y:{direction:'UP',cm:1.1},z:{direction:'FORWARD',cm:11.6}}:null,guidance:active?'Move hand left':null,detail:'Illustrative values only. No live guidance or hardware connection.',audio:{enabled:true,lastCue:active?'MOVE_HAND_LEFT':fresh?'FINDING_PEPSI':'STOP',playback:'Simulated'},voice:{status:'Simulated listening'},wearable:{available:false}};capturedAt=performance.now();roundTrip=0;render();};sample();setInterval(sample,100);
 notify('SIMULATED design preview. Controls do not operate the camera, audio or hardware.');
}else void poll();
setInterval(render,50);
