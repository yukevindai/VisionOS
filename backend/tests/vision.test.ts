import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import schema from "../convex/schema";
import { MAX_IMAGE_BYTES, MAX_RESPONSE_BYTES, type VisionRequest } from "../convex/lib/visionService";

// SIMULATED HTTP replies exercise the authenticated proxy, not the Gemini service.
const modules = import.meta.glob("../convex/**/*.ts");
const args: VisionRequest = { imageBase64: Buffer.from([0xff, 0xd8, 0xff, 0]).toString("base64"),
  frameId: "native-frame-1", capturedAtMs: 1_800_000_000_000, requestId: "read-1" };
const token = "SIMULATED-server-token-00000000000";
const reply = () => ({ requestId: args.requestId, screenModel: {
  schemaVersion: 3, frameId: args.frameId, capturedAtMs: args.capturedAtMs,
  panelType: "controlPanel", coordinateSystem: "normalizedImageTopLeft", source: "visionModel",
  availability: "available", isPaymentScreen: false, controls: [], sections: [],
  evidence: { provider: "gemini", model: "SIMULATED-provider", live: true },
} });
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  vi.stubEnv("R3_VISION_URL", "https://simulated-service.invalid/v1/read-screen");
  vi.stubEnv("R3_VISION_SERVICE_TOKEN", token);
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });
async function signedIn() {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://simulated-auth.invalid/", subject: "owner" });
  await owner.mutation(api.users.ensure, {});
  return { t, owner };
}

describe("authenticated vision bridge", () => {
  it("requires an existing account before image/provider work", async () => {
    const t = convexTest(schema, modules);
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    await expect(t.action(api.vision.readScreen, args)).rejects.toThrow("Authentication required");
    const unknown = t.withIdentity({ issuer: "https://simulated-auth.invalid/", subject: "new-account" });
    await expect(unknown.action(api.vision.readScreen, args)).rejects.toThrow("Account setup required");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("reads privately without a media session and keeps credentials server-side", async () => {
    const { t, owner } = await signedIn();
    const fetch = vi.fn(async (_url: string, _init: RequestInit) => json(reply())); vi.stubGlobal("fetch", fetch);
    const result = await owner.action(api.vision.readScreen, args);
    expect(result).toEqual(reply());
    expect(fetch).toHaveBeenCalledExactlyOnceWith("https://simulated-service.invalid/v1/read-screen", expect.objectContaining({
      method: "POST", redirect: "error",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    }));
    expect(JSON.parse(fetch.mock.calls[0]![1].body as string)).toEqual(args);
    expect(JSON.stringify(result)).not.toContain(token);
    expect(await t.run(ctx => ctx.db.query("sessions").collect())).toEqual([]);
  });
  it("rejects invalid payloads before provider work", async () => {
    const { owner } = await signedIn();
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const change of [
      { imageBase64: "junk" }, { imageBase64: args.imageBase64 + "\n" },
      { imageBase64: Buffer.alloc(MAX_IMAGE_BYTES + 1).toString("base64") },
      { frameId: "" }, { requestId: "x".repeat(129) }, { capturedAtMs: -1 }, { capturedAtMs: 1.5 },
    ]) await expect(owner.action(api.vision.readScreen, { ...args, ...change })).rejects.toThrow("INVALID_VISION");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("requires a configured HTTPS endpoint", async () => {
    const { owner } = await signedIn();
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const url of ["", "http://localhost/v1/read-screen", "https://secret@host.invalid/v1/read-screen", "https://host.invalid/other"]) {
      vi.stubEnv("R3_VISION_URL", url);
      await expect(owner.action(api.vision.readScreen, args)).rejects.toThrow("VISION_NOT_CONFIGURED");
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects mismatched correlation and simulated service replies", async () => {
    const { owner } = await signedIn();
    for (const value of [
      { ...reply(), requestId: "other" },
      { ...reply(), screenModel: { ...reply().screenModel, frameId: "other" } },
      { ...reply(), screenModel: { ...reply().screenModel, capturedAtMs: args.capturedAtMs + 1 } },
      { ...reply(), screenModel: { ...reply().screenModel, source: "SIMULATED" } },
      { ...reply(), screenModel: { ...reply().screenModel, isPaymentScreen: "false" } },
      { ...reply(), screenModel: { ...reply().screenModel, controls: [null] } },
    ]) {
      vi.stubGlobal("fetch", vi.fn(async () => json(value)));
      await expect(owner.action(api.vision.readScreen, args)).rejects.toThrow("INVALID_VISION_RESPONSE");
    }
  });
  it("keeps provider-unavailable distinct from an absent target", async () => {
    const { owner } = await signedIn();
    const value = { ...reply(), screenModel: { ...reply().screenModel, availability: "unavailable", unavailableReason: "providerUnavailable" } };
    vi.stubGlobal("fetch", vi.fn(async () => json(value)));
    expect(await owner.action(api.vision.readScreen, args)).toEqual(value);
  });
  it("bounds response bytes and rejects malformed or failed HTTP", async () => {
    const { owner } = await signedIn();
    for (const [response, code] of [
      [new Response("secret failure", { status: 401 }), "VISION_SERVICE_UNAVAILABLE"],
      [new Response("[]", { headers: { "Content-Type": "application/json" } }), "INVALID_VISION_RESPONSE"],
      [new Response("x".repeat(MAX_RESPONSE_BYTES + 1), { headers: { "Content-Type": "application/json" } }), "VISION_RESPONSE_TOO_LARGE"],
    ] as const) {
      vi.stubGlobal("fetch", vi.fn(async () => response));
      await expect(owner.action(api.vision.readScreen, args)).rejects.toThrow(code);
    }
  });
  it("aborts a stalled provider call at six seconds", async () => {
    const { owner } = await signedIn();
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(new Error("aborted")));
    })));
    const pending = expect(owner.action(api.vision.readScreen, args)).rejects.toThrow("VISION_TIMEOUT");
    await vi.advanceTimersByTimeAsync(6_001);
    await pending;
  });
});
