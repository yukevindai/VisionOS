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
