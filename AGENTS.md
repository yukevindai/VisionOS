# Repository instructions

Start each coherent change on a fresh, short-lived branch from current `origin/main`. Push that branch and open a pull request; do not push directly to `main` or reuse a branch after its PR is merged. Merge through GitHub after one approval and the required checks pass. Keep unrelated changes in separate PRs.

The required CI checks are `Generated Xcode project` and `Swift package tests`. The full `iOS CI` workflow still runs app tests, UI/accessibility audits, and an unsigned Release build; inspect its results and disclose failures in the PR. It is not a required merge check while the documented contrast audits remain unresolved. Do not suppress those audits or describe a passing required-checks workflow as a full iOS or live-device pass.

Read `docs/REPOSITORY_STRUCTURE.md` and the applicable specification in `docs/` before changing code. Preserve the team ownership and safety boundaries in `docs/Cal_Hacks_13_Build_Spec_v3.md`. R2 targets native iPhone iOS, despite the repository name.

If `ios/R2_RELAY.md` exists locally, read it first for session continuity. Keep it updated before ending a session or approaching the context limit. Preserve unfinished work, exact next steps and actual validation results. Do not treat a simulation pass as a live-device pass.

`ios/R2_RELAY.md` and `ios/R2_HANDOFF.md` are local-only and ignored by Git. Do not commit, force-add, push, or delete them. Their absence in a fresh clone is expected; use the public specifications, `ios/README.md`, and validation records instead.

The user authorizes complexity-based sub-agent delegation. Coordinate disjoint file ownership, read existing changes before editing, and do not fabricate external values or overwrite teammate modules. Apply relevant installed skills when helpful; preserve the native iPhone platform. Installing a skill alone does not authorize unrelated publishing, deployment or communication workflows.
