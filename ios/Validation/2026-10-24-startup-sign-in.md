# Startup sign-in — October 24, 2026

Reflex now offers Sign in before the Blind / Not blind role choices. It reuses the existing native Auth0 PKCE flow and Keychain storage, and ensures the authenticated account in Convex before continuing. A restored account skips the welcome screen only after that setup succeeds. Cancelled login and failed account setup remain retryable. Continue without signing in preserves local camera and Stop access; it does not grant backend authorization. Explicit simulation retains its existing direct role flow.

The configured Call introduction no longer tells already signed-in users to sign in. Backend authorization, provider credentials, perception, guidance and Stop behavior are unchanged in this PR.

## Validation

- Eight focused startup model tests passed: signed-out launch, stored-account restoration, explicit login, cancelled login retry, account-setup retry, skip during restoration, repeated login suppression, and unavailable configuration.
- Four focused simulator UI tests passed: startup sign-in/guest entry, largest-text startup accessibility audit (no exclusions), existing role flow, and image-reader entry/close.
- Signed Debug iPhone build passed with this change combined with the preview fix and teammate/Gemini integration candidates (PRs 3, 20, 21, 22).
- Project generation is deterministic and diff checks pass. The startup screen was visually inspected in simulator screenshots at normal and largest text sizes.
- These tests use injected identities or simulator navigation. They do not establish successful real Auth0 login or live Gemini object recognition.
- Existing full iOS contrast-audit failures remain outside this change; no audits are suppressed.
