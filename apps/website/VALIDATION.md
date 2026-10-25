# Website refresh validation

Validated on 2026-10-25 against main `420245f`, scoped to `apps/website/`.

## Completed

- `npm run build`: passed with the existing dependency-free build.
- `git diff --check`: passed.
- Chromium visual review: desktop, mobile, and dark appearance.
- Responsive checks at 320, 375, 768, 1024, and 1440 CSS pixels in both light and dark appearance: no horizontal overflow, all images loaded.
- Keyboard: first Tab reaches the skip link; activation focuses main content; Enter toggles native disclosure sections.
- Axe automated checks: no violations on the home page in light/dark appearance, or on the accessibility page in light appearance.
- Local anchors, unique IDs, image paths, and alt/dimension attributes: passed.
- Preservation: all four pre-existing photographs remain displayed. All eight original WebP files are byte-for-byte unchanged.
- Lighthouse mobile lab run: accessibility 100, best practices 100, SEO 100. Reported FCP 0.8s, LCP 3.2s, CLS 0, and total blocking time 0ms. The environment did not produce an aggregate performance score; these are local lab observations, not production performance guarantees.

Browser tooling was installed outside the repository. No runtime dependencies were added. The logo was optimized to a 256px PNG (about 32KB).

## Limits

Automated accessibility checks do not constitute a formal audit or screen-reader usability testing. This website change does not validate the hardware, native iPhone app, cloud services, or Family Notes/Voice on live devices. Deployment and repository-wide CI results must be checked on the PR separately.
