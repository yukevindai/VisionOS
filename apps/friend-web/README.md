# VisionOS Friend browser viewer

This is the minimal watch-only browser helper for a real iPhone → friend video demo. It uses Auth0 Universal Login (authorization code with PKCE through `@auth0/auth0-react`/`auth0-spa-js`), authenticated Convex functions, and LiveKit's browser SDK. It never requests the helper's camera or microphone and does not implement target selection, model calls, or audio. A friend can redeem an invitation, browse authorized sessions, join one, watch the owner's video, and leave.

## Run locally

1. In Auth0, create a **Single Page Application**. Add `http://localhost:5173` to Allowed Callback URLs, Allowed Logout URLs, and Allowed Web Origins. Use this exact hostname with the local Vite server. Production hosting must be HTTPS and its exact origin must be separately registered.
2. Configure the backend Auth0 issuer and this web client ID as described in [`backend/README.md`](../../backend/README.md). Configure Convex and LiveKit server-side. The web app never receives a LiveKit API secret.
3. Copy `.env.example` to `.env.local` and fill the **public** Auth0 domain, SPA client ID, and Convex deployment URL. Keep real values untracked. No provider secret belongs in this file.
4. From this directory, run `pnpm install --frozen-lockfile` and `pnpm dev`. Open the exact registered origin. `pnpm build` creates a static production bundle in `dist/`.

The backend's `assistance.helperStatus` gives the authenticated reserved/connected helper a nonce-correlated, bounded status. The browser uses it to verify provider-confirmed membership and close on ended sharing, revocation, lease expiry, or media generation change. The owner iPhone must be running an authenticated session with a ready shared LiveKit room and a current sharing grant. This source/build alone is not a live service test.

An invitation link can carry `?invite=<code>`; the helper must still press **Accept**, and unbound codes require owner approval. Discovery is paginated; an empty page can be followed by another page. After pressing **Join**, media is authorized for one reservation and one room generation. The viewer never rejoins automatically after expiry or a disconnect.

For a live demo, test actual Auth0 callback, Convex authentication and deployment, LiveKit webhook reconciliation, iPhone owner publisher, helper join and removal, and video cutoff on Stop/End with real accounts/devices. Browser build only confirms local source validity.
