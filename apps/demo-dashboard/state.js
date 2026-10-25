/** Display-only freshness projection. Receipt time never makes sensor evidence newer. */
export function projectSnapshot(snapshot, elapsedMs = 0) {
  const s = snapshot || {};
  const fresh = s.connected === true && s.fresh === true &&
    Number.isFinite(s.validForMs) && s.validForMs > Math.max(0, elapsedMs);
  const axes = fresh && s.tracking === 'LOCKED' && s.axes ? s.axes : null;
  return {...s, fresh, axes, frame: fresh ? s.frame : null,
    guidance: axes ? s.guidance : null,
    distanceCm: axes && Number.isFinite(s.distanceCm) ? s.distanceCm : null,
    depth: axes ? 'Validated' : 'Unavailable',
    frameAgeMs: Number.isFinite(s.frameAgeMs) ? s.frameAgeMs + Math.max(0, elapsedMs) : null};
}
export function axisValue(axis) {
  return axis && Number.isFinite(axis.cm) ? `${Math.abs(axis.cm).toFixed(1)} cm` : '—';
}
export function stageStates(s) {
  return [s.fresh ? 'Current frame' : 'Waiting', s.fresh && s.tracking === 'LOCKED' ? 'Selected' : 'Waiting',
    s.axes ? 'Tracked' : 'Not verified', s.axes ? 'Validated' : 'Unavailable', s.guidance ? 'Reported by pipeline' : 'Paused'];
}

/** Text only; model prose cannot become guidance or markup. */
export function agentView(value, connected = true) {
  if (!connected || !value) return {status: 'Orchestration unavailable', goal: '', response: '', events: []};
  const clean = text => String(text ?? '').slice(0, 1000);
  const current = value.active_agent || value.agent || 'Coordinator';
  const state = value.status || value.state || 'idle';
  const latency = Number.isFinite(value.latency_ms) ? ` | ${Math.round(value.latency_ms)} ms` : '';
  return {status: `${clean(current)}: ${clean(state)}${latency}${value.error ? ` | ${clean(value.error)}` : ''}`,
    goal: clean(value.current_goal || value.goal), response: clean(value.response || value.last_response || value.clarification),
    events: (Array.isArray(value.events) ? value.events : []).slice(-6).map(event => {
      const elapsed = Number.isFinite(event.latency_ms) ? ` (${Math.round(event.latency_ms)} ms)` : '';
      return `${clean(event.agent || event.source || event.from || 'Coordinator')} -> ${clean(event.destination || event.to || event.event || event.status || event.kind)}${elapsed}${event.error ? `: ${clean(event.error)}` : ''}`;
    })};
}
