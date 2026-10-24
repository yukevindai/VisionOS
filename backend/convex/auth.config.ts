import type { AuthConfig } from "convex/server";

const issuer = process.env.AUTH0_ISSUER;
const nativeClientId = process.env.AUTH0_NATIVE_CLIENT_ID;
const webClientId = process.env.AUTH0_WEB_CLIENT_ID;

if (!issuer || !nativeClientId || !webClientId) {
  throw new Error("AUTH0_ISSUER, AUTH0_NATIVE_CLIENT_ID and AUTH0_WEB_CLIENT_ID must be configured");
}
const issuerUrl = new URL(issuer);
if (issuerUrl.protocol !== "https:" || issuerUrl.href !== issuer) throw new Error("AUTH0_ISSUER must be an exact HTTPS issuer URL");

const config = {
  providers: [
    { domain: issuer, applicationID: nativeClientId },
    { domain: issuer, applicationID: webClientId },
  ],
} satisfies AuthConfig;

export default config;
