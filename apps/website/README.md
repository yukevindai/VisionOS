# VisionOS company website

Public product and mission website, implemented from the [approved desktop/mobile Figma design](https://www.figma.com/design/nsDazJM8jtWNRUM9JJT0SS?node-id=2-134). This is separate from the live demo dashboard, friend web app, backend, iPhone app and hardware runtime. It does not access cameras or microphones or operate the hardware.

## Local preview

Requires Node.js 22+ and Python 3 for the optional preview server. There are no npm dependencies or installation steps.

```sh
cd apps/website
npm run build
npm run preview
```

Open http://localhost:3000. Content is semantic HTML with responsive CSS; no client JavaScript is required. Fonts (Manrope and IBM Plex Sans) load through Google Fonts with local sans-serif fallbacks. The site works without the font service.

## Vercel

Import `yukevindai/VisionOS` as a separate Vercel project, with **Root Directory `apps/website`**, **Framework Preset Other**, build command `npm run build`, and output directory `dist`. The checked-in `vercel.json` sets the build/output and response headers. Use `main` as the production branch after review/merge, and PR branches for previews. No environment variables, product credentials, or server runtime are required. This commit does not create a Vercel project or deploy the site.

## Photos and content

Only the four user-supplied hardware photographs are used. Optimized WebP copies preserve the originals’ content; EXIF orientation is applied and metadata omitted. The source uploads remain with the owner.

| Source | Website asset | Placement |
| --- | --- | --- |
| IMG_1728.jpeg | hardware-1728-{640,1280}.webp | Hero hardware overview |
| IMG_1730.jpeg | hardware-1730-{640,1280}.webp | Actuator assembly feature |
| IMG_1729.jpeg | hardware-1729-{640,1280}.webp | Enclosure gallery |
| IMG_1731.jpeg | hardware-1731-{640,1280}.webp | Connected assembly gallery |

All are photos of the hardware, not user-testing photos. Do not substitute stock/generated images or the older laptop photos. `srcset` provides two sizes; gallery images show the full photo while hero/feature images crop as approved in Figma.

## Launch notes

- Confirm final prototype wording with the team before public launch. Copy distinguishes prototype capabilities from the recent-events direction still being developed.
- The team inbox and domain have not been supplied. The contact CTA honestly links to public GitHub issue creation (GitHub account required), not a fake email or submission form. Replace it with an approved inbox when available.
- No testimonials, adoption numbers, member names, or certification claims have been invented.
- `accessibility.html` describes site behavior and provides a feedback route, without claiming formal certification.
- No existing product feature may be removed or disabled without Kevin’s explicit instruction.
