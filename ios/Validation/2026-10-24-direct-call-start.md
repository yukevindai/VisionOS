# Direct Call startup — October 24, 2026

Call no longer opens mandatory setup or offers a disabled speech-only Continue
button. It requests missing camera, microphone and speech-recognition access and
continues the same startup when authorized and foreground-active. Denied or
restricted access produces a specific Home error; denied access offers Open
Settings. Hand/position preferences and optional band setup remain in Settings,
including after a failed Call. No physical-guidance or calibration gates changed.

Permission-prompt time does not consume the adapter startup timeout. Repeated Call
is serialized. End/background invalidates pending startup, and late grants cannot
restart it. Immediate End before the permission task starts avoids opening prompts.
System-alert inactivity is tolerated only during pending permission startup;
ordinary active-call inactivity still pauses assistance.

Validation on Xcode 27.1:

- 171 app unit tests passed, including fresh-preference startup, automatic access,
  foreground ordering, duplicate Call, immediate End, late grants, background,
  denial and active-call interruption cases.
- Five focused permission UI tests passed (automatic request, denied both,
  camera denial, microphone denial, restricted policy).
- Largest-text Home denial → edit Left/Mounted in Settings → permission recovery
  → active session → End passed in a focused rerun. The first run failed because
  the scrolling helper overshot long text; a slow drag with a hold corrected it.
  Visibility assertions and all accessibility audit checks remain enabled.
- Guidance 11, Targets 20, Tasks 92 and HapticLink 44 tests passed (167 total).
- Signed physical iPhone build and installation succeeded. The user confirmed
  Call reaches the call screen after allowing system prompts. This is startup
  acceptance, not object-recognition, speech accuracy or guidance acceptance.
- The initial post-install launch received an iOS signing/trust refusal despite a
  valid local signature and matching device profile. The user then opened the
  app successfully; its physical process was observed running.

The final immediate-End guard was added after the user's successful Call check;
its behavior is covered by the final unit suite. Phone builds use the framework
runpath from PR #10 as a command-line override, because this change starts from
main and keeps that independent launch fix in its own PR. Merge PR #10 before
expecting default physical-device builds to launch correctly.

Logs: `/tmp/reflex-direct-call-final-unit.log`,
`/tmp/reflex-direct-call-ui.log`, `/tmp/reflex-direct-call-largest-final.log`,
`/tmp/reflex-direct-call-final-device-build.log`, and
`/tmp/reflex-direct-call-{Guidance,Targets,Tasks,HapticLink}.log`.

Full simulator UI/audit and Release suites were not rerun for this change.
Three historical contrast audit failures remain unresolved and unsuppressed.
No hosted CI pass or full live-device acceptance is claimed.
