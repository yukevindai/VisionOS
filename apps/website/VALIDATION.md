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

## 21st.dev component enhancement (2026-10-25)

Based on main `1f30dc9`; all existing photo assets and the logo remain byte-for-byte unchanged. The original copy, sections, links, and native disclosures are retained.

- Build, JavaScript syntax, and diff checks passed.
- Chromium browser assertions passed at 320, 375, 768, 1024, and 1440px in light and dark appearance; no page or modal horizontal overflow.
- All four photo links: Enter opens the correct image, arrow keys cycle images, Escape closes, and focus returns to the opener. Close and backdrop dismissal also work; modal controls keep keyboard navigation contained.
- Navigation highlight responds to keyboard focus; CTA hover transitions render correctly.
- Reduced-motion preference suppresses modal entrance and navigation animation; no-JavaScript photo links open the full original image.
- Axe found no violations on home in both themes, the open photo viewer, or the accessibility page. No JavaScript page errors were observed.
- Desktop and mobile viewer screenshots were visually reviewed.
- Lighthouse mobile: accessibility 100, best practices 100, SEO 100; FCP 0.8s, LCP 3.3s, CLS 0, total blocking time 0ms. Aggregate performance remains unavailable because this browser did not provide filmstrip screenshots. Lighthouse also recommends CSS minification. These are local lab results, not production guarantees.

The native component adapters add approximately 5.3KB of uncompressed, deferred JavaScript and no package dependencies or runtime CDN calls. Source links and MIT notices are included in `THIRD_PARTY_NOTICES.md` and copied into the built site.
