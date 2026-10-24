from dataclasses import dataclass
import math


@dataclass(frozen=True)
class GuidanceDecision:
    delta_m: tuple[float,float,float] | None
    distance_m: float | None
    reason: str


def evaluate(target,finger,object_state,frame_age_s,max_age_s=.2,completed=False,
             *,hazard_active=False,task_generation=None,evidence_generation=None):
    if hazard_active:
        return GuidanceDecision(None,None,'hazard_stop')
    if task_generation is not None and task_generation!=evidence_generation:
        return GuidanceDecision(None,None,'task_generation_mismatch')
    if completed:
        return GuidanceDecision(None,None,'completed')
    if not math.isfinite(frame_age_s) or not 0<=frame_age_s<=max_age_s:
        return GuidanceDecision(None,None,'stale_frame')
    if object_state not in ('VISIBLE','TRACKED'):
        return GuidanceDecision(None,None,'object_unverified')
    if target is None or finger is None:
        return GuidanceDecision(None,None,'missing_measurement')
    if len(target)!=3 or len(finger)!=3 or not all(math.isfinite(float(x)) for x in (*target,*finger)):
        return GuidanceDecision(None,None,'invalid_geometry')
    delta=tuple(float(t)-float(f) for t,f in zip(target,finger))
    return GuidanceDecision(delta,math.sqrt(sum(x*x for x in delta)),'measured')
