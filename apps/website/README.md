# VisionOS company website

Public product and mission website. This refresh builds on the original [Figma design](https://www.figma.com/design/nsDazJM8jtWNRUM9JJT0SS?node-id=2-134), with a new visual direction and copy reviewed against main at `420245f`.

The website is separate from the demo dashboard, helper apps, backend, native iPhone app, and hardware runtime. It does not access cameras or microphones or operate hardware.

## Local preview

Requires Node.js 22+ and Python 3 for the optional preview server. There are no npm dependencies or installation steps.

```sh
cd apps/website
npm run build
npm run preview
```

Open http://localhost:3000. Semantic HTML, native disclosure controls, and responsive CSS work without JavaScript. A small deferred `components.js` module progressively enhances navigation highlights and a keyboard-accessible hardware photo viewer. The system font stack avoids third-party font requests. Appearance follows the device's light/dark preference; reduced motion and forced-colors preferences are supported.

## Design direction

A calm, spacious product site with a consistent blue accent, large readable typography, and real hardware photography. Inspired by the focused navigation and clear hierarchy of [DeepSeek](https://www.deepseek.com/en/), the people-first presentation of [Be My Eyes](https://www.bemyeyes.com/), and the application-focused storytelling of [Microsoft AI for Good](https://www.microsoft.com/en-us/corporate-responsibility/topics/ai-for-good-lab/). These are design references, not affiliations or endorsements.

The UI/UX Pro Max minimal/Swiss direction and design-taste audit informed the redesign. Native CSS preserves the existing dependency-free stack. Controls use a 12px radius and media/panels use 24px. Motion is limited to interaction feedback; there are no automatic effects or scroll hijacking.

## 21st.dev component enhancements

- Magic UI Interactive Hover Button: layered fill and label transitions on the two primary CTAs.
- Motion Primitives Animated Background: a shared highlight follows navigation hover and keyboard focus.
- Motion Primitives Morphing Dialog pattern: all four hardware photos open in a native modal viewer with previous/next controls, arrow keys, Escape, and focus restoration.

These are lightweight native adaptations of the reviewed components, not React library installations. Reduced motion disables transitions; native photo links and all original content work without JavaScript. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) records sources, adaptation details, and the MIT licenses.

## Photos and logo

All four original hardware photographs and all eight optimized WebP assets are preserved. The photographs are hardware images, not user-testing photos.

| Source | Website asset | Placement |
| --- | --- | --- |
| IMG_1728.jpeg | hardware-1728-{640,1280}.webp | Hero overview |
| IMG_1730.jpeg | hardware-1730-{640,1280}.webp | Step-by-step reach guidance |
| IMG_1729.jpeg | hardware-1729-{640,1280}.webp | Enclosure gallery |
| IMG_1731.jpeg | hardware-1731-{640,1280}.webp | Connected assembly gallery |
| User-supplied eye logo | visionos-logo.png | Navigation, footer, contact, favicon |

The uploaded logo had an opaque checkerboard background. An image-generation background-removal edit prepared the transparent website version from that supplied mark. The generated result was optimized to a 256px PNG for delivery. The source upload remains with the owner. No stock or generated hardware photographs were added. Responsive `srcset`, image dimensions, and lazy loading are retained; gallery images show the full photo.

## Content and implementation boundaries

The mission focuses on access to spatial information and user agency. It does not generalize that blind people lack spatial knowledge.

- Object finding, scene description, and recent visible-event recall reflect `backend/r3_vision/visual_agents.py` and `docs/MULTI_AGENT_ORCHESTRATION.md`.
- Step-by-step hand guidance depends on camera visibility, valid observations, calibration, and available depth. The user moves their hand; the hardware provides directional cues.
- The current Arduino integration uses four servos (`backend/r3_vision/arduino_ble.py` and `firmware/Arduino101ServoController/`). Do not describe it as confirmed vibration motors or a mechanism that physically moves a user's hand.
- Family Notes and consented AI voice copies reflect `docs/FAMILY_NOTES.md` and `docs/FAMILY_VOICE.md`. Live integration validation is still pending; merged code is not a consumer launch.
- Button, door, and kiosk interactions remain broader goals rather than completed real-world demonstrations.
- No testimonials, adoption numbers, certifications, or unverified performance claims are used.

## Validation

See [VALIDATION.md](VALIDATION.md) for browser, responsive, accessibility, and preservation checks and their limits.

## Deployment

Vercel settings: **Root Directory `apps/website`**, **Framework Preset Other**, build command `npm run build`, output directory `dist`. The checked-in `vercel.json` contains the build/output settings and response headers. Production uses `main` after review/merge; PR branches may create previews if configured. No environment variables or product credentials are required.

The team inbox has not been supplied. The contact CTA opens public GitHub issue creation, with the account requirement disclosed. `accessibility.html` provides site-use information and a feedback route without claiming certification.

No existing product feature may be removed or disabled without Kevin's explicit instruction.
