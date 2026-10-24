import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import schema from "../convex/schema";
import { simulatedWav } from "../convex/lib/cartesia";

// SIMULATED: no test reaches Cartesia or spends credits.
const modules = import.meta.glob("../convex/**/*.ts");
const args = { requestId: "speech-1", text: "Found Coke, 2.49. Should I guide you there?" };
const KEY = "SIMULATED-cartesia-key-0000";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
async function signedIn() {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://simulated-auth.invalid/", subject: "owner" });
  await owner.mutation(api.users.ensure, {});
  return owner;
}

describe("speech:synthesize", () => {
  it("requires an existing account before any provider work", async () => {
    const t = convexTest(schema, modules);
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    vi.stubEnv("CARTESIA_API_KEY", KEY);
    await expect(t.action(api.speech.synthesize, args)).rejects.toThrow("Authentication required");
    const unknown = t.withIdentity({ issuer: "https://simulated-auth.invalid/", subject: "new-account" });
    await expect(unknown.action(api.speech.synthesize, args)).rejects.toThrow("Account setup required");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("returns SIMULATED audio when no key is configured", async () => {
    vi.stubEnv("CARTESIA_API_KEY", "");
    const owner = await signedIn();
    expect(await owner.action(api.speech.synthesize, args)).toMatchObject({ ok: true, mode: "SIMULATED", requestId: "speech-1" });
  });

  it("returns real-mode WAV and keeps the key server-side", async () => {
    vi.stubEnv("CARTESIA_API_KEY", KEY);
    vi.stubEnv("CARTESIA_VOICE_ID", "SIMULATED-voice");
    vi.stubEnv("CARTESIA_MODEL", "SIMULATED-model");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array(simulatedWav()))));
    const owner = await signedIn();
    const result = await owner.action(api.speech.synthesize, args);
    expect(result).toMatchObject({ ok: true, mode: "REAL_API", format: "wav" });
    expect(JSON.stringify(result)).not.toContain(KEY);
  });

  it("returns a fallback error code instead of throwing when the provider fails", async () => {
    vi.stubEnv("CARTESIA_API_KEY", KEY);
    vi.stubEnv("CARTESIA_VOICE_ID", "SIMULATED-voice");
    vi.stubEnv("CARTESIA_MODEL", "SIMULATED-model");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("quota", { status: 402 })));
    const owner = await signedIn();
    expect(await owner.action(api.speech.synthesize, args)).toMatchObject({ ok: false, errorCode: "PROVIDER_ERROR", httpStatus: 402 });
  });
});
