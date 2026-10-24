# Reflex guidance interface proposal v0 — independently authored

Design proposal only; no adapter, BLE implementation, or shared team contract change. It imports no research source. R1/R2/H1 must agree integration and pattern mapping separately.

One immutable observation contains:
- schema_version, sequence, task_generation, source_frame_id, coordinate_frame_id.
- hand_xyz_m and target_xyz_m: nullable [x,y,z], meters; same camera frame, x right, y down, z forward. Target is an observed reachable surface. Pose transforms must be explicit.
- relative_xyz_m = target - hand and distance_m = norm(relative): null unless compatible positions exist.
- hand_confidence and target_confidence: nullable [0,1], with source/definition; never invent a confidence from visibility.
- hand_depth_source and target_depth_source: lidar, size_estimated, unavailable; source timestamps for each position, monotonic clock identity, received/published time, age_ms, synchronization uncertainty. Unknown absolute capture age must remain unknown.
- hand_tracking and target_tracking: observed, predicted, lost; guidance_direction: left/right, up/down, forward/back or none. Positive dx=right, dy=down, dz=forward.
- guidance_state: ACTIVE or STOP, stop_reason, valid_until_monotonic, evidence_kind (measured/estimated), and confirmed_observation flag.

Consumer recomputes age at consumption. ACTIVE requires valid finite, synchronized, current observed metric positions, matching generation/frame convention, valid depth/confidence and age <=300 ms. Estimated, predicted, missing, stale, ambiguous or incompatible evidence produces STOP; display-only estimates must be explicitly labeled. Baseline scene.hand_xyz, scene.object_xyz and scene.aim_xyz are candidate inputs, not automatically trusted: choose aim only with verified reachable-surface semantics and inspect their depth/source/age before conversion.

STOP preempts output, invalidates pending work by generation and sends ALL_STOP to both wristbands through R2 HapticLink. Only R1 scheduler releases movement cues. Expiry, disconnect or tracking loss stops output; H1 firmware independently enforces TTL/heartbeat/duty limits. BLE encoding and actuator patterns remain H1-owned/TBD. No overlap/proximity state is grasp success.

This contract can support later left/right wristband guidance from validated displacement and direction, while native iOS supplies camera/LiDAR/hand observations directly through Apple frameworks. Raw baseline idle/grab/phrase outputs do not authorize haptics. Wristbands and iOS are not built in this task.
