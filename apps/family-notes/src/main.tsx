import { Auth0Provider } from "@auth0/auth0-react";
import { ConvexReactClient } from "convex/react";
import { ConvexProviderWithAuth0 } from "convex/react-auth0";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./style.css";

const domain = import.meta.env.VITE_AUTH0_DOMAIN?.trim();
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID?.trim();
const convexUrl = import.meta.env.VITE_CONVEX_URL?.trim();
const root = createRoot(document.getElementById("root")!);

if (!domain || !clientId || !convexUrl) {
  root.render(<main className="shell" role="main"><h1>VisionOS Family Notes needs configuration</h1>
    <p>Set <code>VITE_AUTH0_DOMAIN</code>, <code>VITE_AUTH0_CLIENT_ID</code>, and <code>VITE_CONVEX_URL</code> in <code>.env.local</code>, then restart the browser server.</p>
    <p>See <a href="https://github.com/yukevindai/VisionOS">the project setup</a> or the local README.</p></main>);
} else {
  const convex = new ConvexReactClient(convexUrl);
  root.render(
    <Auth0Provider domain={domain} clientId={clientId}
      authorizationParams={{ redirect_uri: window.location.origin }}
      onRedirectCallback={(state) => window.history.replaceState({}, "", state?.returnTo || "/")}>
      <ConvexProviderWithAuth0 client={convex}><App /></ConvexProviderWithAuth0>
    </Auth0Provider>,
  );
}
