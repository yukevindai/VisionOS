import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import schema from "../convex/schema";

const modules = import.meta.glob("../convex/**/*.ts");

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("Auth0 verifier configuration", () => {
  it("fails closed on missing or non-HTTPS issuer settings", async () => {
    vi.stubEnv("AUTH0_ISSUER", undefined);
    vi.stubEnv("AUTH0_NATIVE_CLIENT_ID", undefined);
    vi.stubEnv("AUTH0_WEB_CLIENT_ID", undefined);
    vi.resetModules();
    await expect(import("../convex/auth.config")).rejects.toThrow("must be configured");

    vi.stubEnv("AUTH0_ISSUER", "http://issuer.example/");
    vi.stubEnv("AUTH0_NATIVE_CLIENT_ID", "native-client");
    vi.stubEnv("AUTH0_WEB_CLIENT_ID", "web-client");
    vi.resetModules();
    await expect(import("../convex/auth.config")).rejects.toThrow("exact HTTPS issuer URL");
  });

  it("registers only the configured native and web client audiences", async () => {
    vi.stubEnv("AUTH0_ISSUER", "https://issuer.example/");
    vi.stubEnv("AUTH0_NATIVE_CLIENT_ID", "native-client");
    vi.stubEnv("AUTH0_WEB_CLIENT_ID", "web-client");
    vi.resetModules();
    const { default: config } = await import("../convex/auth.config");
    expect(config.providers).toEqual([
      { domain: "https://issuer.example/", applicationID: "native-client" },
      { domain: "https://issuer.example/", applicationID: "web-client" },
    ]);
  });
});

describe("authenticated account mapping", () => {
  it("requires a verified identity and does not accept a caller-selected subject", async () => {
    const t = convexTest(schema, modules);
    await expect(t.mutation(api.users.ensure, {})).rejects.toThrow("Authentication required");

    const caller = t.withIdentity({ issuer: "https://issuer.example/", subject: "account-1", name: "Owner" });
    const first = await caller.mutation(api.users.ensure, {});
    const again = await caller.mutation(api.users.ensure, {});
    expect(again._id).toBe(first._id);
    expect(first.automaticSharing).toBe(false);
    expect(first.issuer).toBe("https://issuer.example/");
    expect(first.subject).toBe("account-1");

    const otherIssuer = t.withIdentity({ issuer: "https://other.example/", subject: "account-1", name: "Other" });
    const distinct = await otherIssuer.mutation(api.users.ensure, {});
    expect(distinct._id).not.toBe(first._id);
  });

  it("keeps sharing opt-in scoped to the signed-in account", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer: "https://issuer.example/", subject: "owner" });
    const stranger = t.withIdentity({ issuer: "https://issuer.example/", subject: "stranger" });
    await owner.mutation(api.users.ensure, {});
    await stranger.mutation(api.users.ensure, {});
    await owner.mutation(api.users.setAutomaticSharing, { enabled: true });
    expect((await owner.query(api.users.me, {})).automaticSharing).toBe(true);
    expect((await stranger.query(api.users.me, {})).automaticSharing).toBe(false);
    await expect(t.query(api.users.me, {})).rejects.toThrow("Authentication required");
  });
});
