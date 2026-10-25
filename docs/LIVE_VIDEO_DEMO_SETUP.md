# Real iPhone → trusted friend video setup

The video demo now has an owner iPhone screen, a single rear-camera source, Auth0 login, Convex trust/session controls, and a browser viewer in `apps/friend-web`. It has **not yet passed a live call**: this checkout has no configured cloud projects and the physical iPhone is unavailable. Start here to supply those missing pieces. No Gemini, Claude or Cartesia key is needed for this video-only flow.

The normal assistance flow still awaits teammate native guidance/vision adapters. Use **Share live video with a friend** on the home screen, with simulated assistance switched off. The separate full-screen demo owns one camera and cannot run alongside normal assistance. It provides video, without microphone, local preview, target selection, guidance or wristband output.

## What to create

| Service | Create | Values needed |
| --- | --- | --- |
| Convex | One cloud development project/deployment | HTTPS deployment URL ending `.convex.cloud`; HTTP actions URL ending `.convex.site` |
| Auth0 | One tenant, one **Native** application and one **Single Page Application** | Tenant domain, native client ID, web client ID |
| LiveKit Cloud | One project and server API key pair | Project WebSocket URL, API key, API secret |
| Apple/Xcode | Select your signing team and a unique app bundle ID | Local signing selection; no Apple password or signing key in chat |

Public project URLs, tenant domain and client IDs can be shared for configuration. Put the LiveKit key/secret directly into **Convex server environment variables**. Do not paste secrets into chat or put them in the iOS/browser configuration. No Auth0 client secret is used by these public PKCE clients.

## 1. Auth0 applications

Create the tenant and enable a login connection for both applications. Use two distinct user accounts for the demo: the iPhone owner and the browser helper.

For the **Native** application, first choose the actual Xcode bundle identifier. Add this exact URL to both **Allowed Callback URLs** and **Allowed Logout URLs**, replacing both placeholders:

```text
BUNDLE_ID://AUTH0_TENANT_DOMAIN/ios/BUNDLE_ID/callback
```

The app registers that custom URL scheme and explicitly uses this callback with Auth0.swift. Enable the Refresh Token grant for the native client; it requests `openid profile email offline_access` and uses the SDK Keychain credentials manager. Configure refresh-token rotation/expiration in the application settings. No custom Auth0 API audience is needed: this backend validates the ID token audience against the two application client IDs. See [Auth0's native quickstart](https://auth0.com/docs/quickstart/native/ios-swift/index) and [refresh-token settings](https://support.auth0.com/center/s/article/Refresh-token-and-session-management).

For the **Single Page Application**, add `http://localhost:5173` to **Allowed Callback URLs**, **Allowed Logout URLs**, and **Allowed Web Origins**. Use that exact local origin on the helper Mac. A hosted viewer needs its own exact HTTPS origin registered. The browser uses Auth0 React with Convex's Auth0 provider; see [Convex's Auth0 setup](https://docs.convex.dev/auth/auth0).

## 2. Convex development deployment

Create/select a cloud development deployment in the [Convex dashboard](https://dashboard.convex.dev). Use the real generated URLs, not invented names. From `backend/`, install the pinned dependencies and connect this directory to that project:

```sh
pnpm install --frozen-lockfile
pnpm exec convex dev --configure
```

This is the first actual deployment step; it has **not** been run for you. Follow the CLI account/project prompts and choose the intended cloud deployment. If initial analysis stops because Auth0 environment values are missing, add them in that deployment's dashboard and retry. `convex dev` stays running to sync later changes.

In that deployment's **Settings → Environment Variables**, set:

```text
AUTH0_ISSUER=https://AUTH0_TENANT_DOMAIN/
AUTH0_NATIVE_CLIENT_ID=<Native application's client ID>
AUTH0_WEB_CLIENT_ID=<SPA application's client ID>
LIVEKIT_URL=<LiveKit project's wss:// URL>
LIVEKIT_API_KEY=<server API key>
LIVEKIT_API_SECRET=<server API secret>
```

The issuer's trailing slash is required. `backend/.env.example` is documentation; merely copying it does not configure the deployed server. Confirm the normal `convex dev` deployment completes, including schema/functions/auth configuration. Prior local `codegen` and simulated tests are not deployment evidence.

## 3. LiveKit webhook

Create the project in [LiveKit Cloud](https://cloud.livekit.io). Copy its project URL and server key pair into the Convex variables above. Under LiveKit **Settings → Webhooks**, create a webhook with:

```text
https://ACTUAL_CONVEX_DEPLOYMENT.convex.site/livekit/webhook
```

Select the same **Signing API key** used in Convex. The server verifies the signed raw body before updating membership. See [LiveKit webhook configuration](https://docs.livekit.io/intro/basics/rooms-participants-tracks/webhooks-events/).

Successful room creation alone is insufficient. Check Convex logs and LiveKit room participants during the call: the owner must become provider-confirmed, then the helper must become provider-confirmed before video subscription opens. Do not bypass these checks to make a blank viewer appear connected.

## 4. iPhone configuration and Xcode

Copy `ios/Config/Local.example.xcconfig` to the ignored `ios/Config/Local.xcconfig` if the latter does not already exist. Fill only public values:

```text
REFLEX_BUNDLE_ID = <your unique bundle identifier>
REFLEX_CONVEX_HOST = <actual deployment>.convex.cloud
REFLEX_AUTH0_DOMAIN = <actual tenant domain>
REFLEX_AUTH0_NATIVE_CLIENT_ID = <native client ID>
```

Use hostnames without `https://` here; the plist supplies schemes, and xcconfig treats `//` as a comment. Confirm the registered Auth0 callback uses this exact bundle ID and domain.

Open `ios/VisionOS.xcodeproj`, select **VisionOS**, and choose your connected iPhone. In **Signing & Capabilities**, select your development team and automatic signing. Unlock/trust the phone, enable Developer Mode if requested, and build/run. The last device inventory found an iPhone 14 Pro Max named “Kraft Dinner” but marked it unavailable.

The iOS simulator can show the home/demo/setup UI. It cannot provide this ARKit rear-camera feed. An unsigned iPhone build also does not establish physical-device behavior.

## 5. Browser helper

Copy `apps/friend-web/.env.example` to `.env.local` if absent:

```text
VITE_AUTH0_DOMAIN=<actual tenant domain>
VITE_AUTH0_CLIENT_ID=<SPA client ID>
VITE_CONVEX_URL=https://<actual deployment>.convex.cloud
```

From `apps/friend-web/`:

```sh
pnpm install --frozen-lockfile
pnpm dev
```

Open [the local helper](http://localhost:5173). Restart Vite after changing environment values. The helper requests neither camera nor microphone. A local missing-configuration screen is expected until these values are supplied.

## 6. First real call

1. On iPhone, turn simulation off, open **Share live video with a friend**, and sign in as the owner.
2. Create an invitation code and share it with the intended helper.
3. In the browser, sign in as the other account and redeem the code.
4. On iPhone, refresh friend requests, review the displayed account, and explicitly approve it.
5. Check the per-session sharing consent and press **Start sharing video**. Grant camera access. If the permission dialog leaves the app inactive at completion, return to VisionOS and press Start again.
6. In the browser, refresh available sessions, select the owner's session, and join. Follow subsequent discovery pages if needed.
7. Confirm the helper sees the actual moving phone camera image with correct orientation. Record this as the first real video result only after observing it.
8. Press **End video now** on iPhone. Confirm video clears on the browser. Also check leaving the screen, backgrounding the phone, helper Leave, and signing out. A new explicit Start/Join is required afterward.

If a stage fails, capture the stage, non-secret error and relevant server log. Do not record access tokens, invitation codes, or API secrets in validation files. The immediate next engineering task is to fix any actual integration failure seen in this sequence, not add more backend features.

## Teammate integration boundary

Read-only fetch found `feature/reflex-modular-ui` at `6bf9f1f` with ScreenModel, TargetSelector and `backend/r3_vision`, and `feature/reflex-hand-guidance` at `b9e99f7` with the laptop guidance prototype. Neither branch implements the native camera source. Neither was merged or edited. The user explicitly authorized this minimal camera adapter. Once R1 supplies its native frame stream, replace the demo capture source with that shared source before combining assistance and video; do not run two camera sessions.
