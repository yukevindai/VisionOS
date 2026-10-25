# Call interface refresh — October 24, 2026

## Scope and source

Started `codex/accessible-call-refresh` from `origin/main` at `4978cf26c71756b6e0a8cf4b8c3b37bedaca3084` in an isolated worktree. Existing primary-checkout Info.plist, generated Xcode project and .vscode changes were preserved.

Read the latest Figma current role (11:42), ready (11:57) and calling (11:76) frames before implementation, then revised those frames in place using the two supplied screenshots. Figma: https://www.figma.com/design/L4c5A5bH9pjnDCTFPDcwHs?node-id=11-39

- Compact role picker: only brand, heading and two semantic buttons. Blind is dominant; Not blind remains easily reachable. Purpose text is retained in accessibility hints.
- Call: large blue rounded rectangle on black, with existing role/tools/settings navigation and explicit permission-error recovery.
- Calling: compact status/details at top, full aspect-fit camera, bottom instructions/captions and Stop/Resume/End. Simulation remains visibly identified. Safety commands and coordinator lifecycle are unchanged.
- Camera: choose ultra-wide from the active ARWorldTrackingConfiguration supported rear formats, otherwise wide, then ARKit’s preferred supported rear format. Actual lens and fallback reach diagnostics. No digital zoom value, second camera, or mid-guidance lens switch.

## Validation

- Xcode 27.1, iOS 26.5 simulator (R2 Setup Accessibility).
- Generated Xcode project deterministic; git diff whitespace check passed.
- Swift packages: 167 passed (Guidance 11, Targets 20, Tasks 92, HapticLink 44).
- App unit tests: 199 passed, including 4 camera format policy tests. The focused camera/geometry run separately passed 10 cases.
- Unsigned Release iPhone build passed. This is compilation, not physical-device validation.
- Combined focused UI run: 15 cases, 13 passed; only the existing simulated `session.caption` contrast checks failed in light and dark appearances. Maximum Dynamic Type audit passed. Functional cases passed for role routing/size proportions, Call, Stop/Resume/End, repeated start, helper tool entry, reduced transparency, permissions and maximum-text settings recovery.
- Rendered role targets measured approximately 45% Blind and 14% Not blind of the full simulator screen; Figma uses 50%/15% frame heights. Normal Call fills the available body. Normal and maximum-text screenshots were visually reviewed.
- Final UI rerun after making simulation persistent: role routing/proportions, lifecycle, reduced-transparency and largest-text control reachability all passed (4 cases). The compact badge audit initially reported clipped text; giving it an unconstrained text height and matching its visible label while retaining the longer explanation as a VoiceOver hint resolved that new finding. Final 3-audit rerun: maximum-text audit passed; only the same light/dark simulated-caption contrast failures remain. No audit exclusions or suppressions changed.

The initial UI run exposed an unbounded scroll viewport behind the bottom controls. Removing vertical fixedSize and clipping the bounded ScrollView repaired that overlap. The simulation indicator was also moved into the fixed status area so bottom-anchored content cannot hide it.

Local logs: `/tmp/reflex-call-refresh-{Guidance,Targets,Tasks,HapticLink}.log`, `/tmp/reflex-wide-camera-tests.log`, `/tmp/reflex-call-refresh-final-tests.log`, `/tmp/reflex-call-refresh-release-final.log`, `/tmp/reflex-call-refresh-final-ui.log`, `/tmp/reflex-call-refresh-badge-audits.log`.

## Limits

No physical camera/0.5×, depth alignment, live Gemini, live helper call, VoiceOver listening session, microphone or wristband acceptance was performed. ARKit may expose only the wide camera even on phones with an ultra-wide lens. The preview intentionally preserves all captured pixels instead of cropping to fill a tall phone screen. Figma uses an explicitly simulated illustration.

Existing full iOS audit failures are not suppressed, and a passing required-check workflow is not a full iOS/live-device pass. Normal Call’s Gemini backend/configuration and separate trusted-friend sharing limitations are unchanged.
