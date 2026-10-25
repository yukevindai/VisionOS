# Component sources and licenses

These are native HTML/CSS/JavaScript adaptations, not unmodified React installations. They were discovered through 21st.dev and reviewed against their public upstream sources on 2026-10-25. No 21st.dev API key, paid registry access, telemetry, or runtime CDN is used.

## Magic UI: Interactive Hover Button

- Author: Magic UI; original component credit to Aayush Bharti.
- [21st.dev listing](https://preview.21st.dev/@dillionverma/components/interactive-hover-button)
- [Upstream component](https://github.com/magicuidesign/magicui/blob/main/apps/www/registry/magicui/interactive-hover-button.tsx)
- Reviewed source blob: `6aab0d85a7869227b9602c6c0e6dcc84a15ede31`.
- Adapted in `styles.css` and the primary CTA markup in `index.html`: layered fill and sliding labels. Native anchors retain their destinations, duplicate visual labels are hidden from assistive technology, and focus/reduced-motion/forced-colors states are supported.

MIT License

Copyright (c) Magic UI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Motion Primitives: Animated Background and Morphing Dialog

- Author: Julien Thibeaut (ibelick).
- [21st.dev library](https://preview.21st.dev/@ibelick/library/motion-primitives)
- [Animated Background listing](https://preview.21st.dev/@ibelick/components/animated-background)
- [Animated Background source](https://github.com/ibelick/motion-primitives/blob/main/components/core/animated-background.tsx), reviewed blob `ae0a1ffc5b375ee59744089c83527cdbe6acec8b`.
- [Morphing Dialog source](https://github.com/ibelick/motion-primitives/blob/main/components/core/morphing-dialog.tsx), reviewed blob `02b8e24fc774d30beacb9650ddfe46c6843ae849`.
- Adapted in `components.js` and `styles.css`: a shared navigation highlight follows pointer or keyboard focus; photo links open an animated image dialog. This port uses the Web Animations API and native `dialog.showModal()` instead of React/Motion, with correct title/description IDs, Escape handling, focus restoration, and previous/next controls. It uses a short scale/fade entrance rather than a shared-element morph. Without JavaScript, photo links still open the full original images.

MIT License

Copyright (c) 2024 ibelick

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

