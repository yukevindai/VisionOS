import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import type { Id } from "../convex/_generated/dataModel";
import schema from "../convex/schema";
import { simulatedWav, wavSeconds } from "../convex/lib/cartesia";
import { consentSentence } from "../convex/voices";
import rateLimiter from "@convex-dev/rate-limiter/test";
import actionRetrier from "@convex-dev/action-retrier/test";

// SIMULATED: Cartesia is a stubbed fetch that answers clone, delete and TTS; no credits are spent.
const modules = import.meta.glob("../convex/**/*.ts");
const ISSUER = "https://simulated-auth.invalid/";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });

type Call = { url: string; method: string; body: unknown };

function cartesia(opts: { clone?: () => Response; remove?: () => Response; tts?: (body: { voice: { id: string } }) => Response } = {}) {
  vi.stubEnv("CARTESIA_API_KEY", "SIMULATED-key");
  vi.stubEnv("CARTESIA_VOICE_ID", "SIMULATED-default-voice");
  vi.stubEnv("CARTESIA_MODEL", "SIMULATED-model");
  const calls: Call[] = [];
  const fetch = vi.fn(async (url: string, init: { method: string; body: unknown }) => {
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    calls.push({ url, method: init.method, body });
    if (url.endsWith("/voices/clone")) return opts.clone ? opts.clone() : Response.json({ id: "SIMULATED-lan-voice" });
    if (init.method === "DELETE") return opts.remove ? opts.remove() : new Response(null, { status: 204 });
    return opts.tts ? opts.tts(body) : new Response(new Uint8Array(simulatedWav()));
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

async function family() {
  const t = convexTest(schema, modules);
  rateLimiter.register(t, "rateLimiter");
  actionRetrier.register(t, "actionRetrier");
  const as = (subject: string, name: string) => t.withIdentity({ issuer: ISSUER, subject, name });
  const owner = as("minh", "Minh"), lan = as("lan", "Lan");
  for (const user of [owner, lan]) await user.mutation(api.users.ensure, {});
  const id = (subject: string) => t.run(async (ctx) => (await ctx.db.query("users")
    .withIndex("by_auth", (q) => q.eq("issuer", ISSUER).eq("subject", subject)).unique())!._id);
  const ownerId = await id("minh"), lanId = await id("lan");
  await t.run((ctx) => ctx.db.insert("friendships", {
    ownerId, helperId: lanId, state: "accepted", notify: false, createdAt: 1, updatedAt: 1 }));
  const upload = (bytes: Uint8Array) => t.run((ctx) => ctx.storage.store(new Blob([bytes.slice()], { type: "audio/wav" })));
  const storageCount = () => t.run(async (ctx) => (await ctx.db.system.query("_storage").collect()).length);
  const settle = () => t.finishAllScheduledFunctions(vi.runAllTimers);
  return { t, owner, lan, ownerId: ownerId as Id<"users">, lanId, upload, storageCount, settle };
}

const ttsCalls = (calls: Call[]) => calls.filter((c) => c.url.endsWith("/tts/bytes")) as
  Array<Call & { body: { voice: { id: string }; transcript: string; generation_config?: { speed?: number; volume?: number } } }>;
const note = { text: "The white bottle on the desk is your medicine water.", kind: "warning" as const };

describe("family voice", () => {
  it("asks the helper to read a consent sentence that names them, the purpose and the way out", async () => {
    cartesia();
    const { lan } = await family();
    const mine = await lan.query(api.voices.mine, {});
    expect(mine.hasVoice).toBe(false);
    expect(mine.consentText).toBe(consentSentence("Lan"));
    expect(mine.consentText).toMatch(/^My name is Lan\. .*only to read my own notes to my family\. I can delete it at any time\.$/);
  });

  it("clones from the consented recording, deletes the recording, and reads new notes in the helper's voice", async () => {
    vi.useFakeTimers();
    const calls = cartesia();
    const { owner, lan, ownerId, upload, storageCount, settle } = await family();
    const storageId = await upload(simulatedWav(12_000));
    expect(await lan.action(api.voicesNode.create, { storageId, consent: true })).toEqual({ ok: true, seconds: 12 });
    expect(await storageCount()).toBe(0); // only the voice ID remains
    const clone = calls.find((c) => c.url.endsWith("/voices/clone"))!;
    expect(clone.method).toBe("POST");
    expect((clone.body as FormData).get("language")).toBe("en");
    expect(String((clone.body as FormData).get("description"))).toContain(consentSentence("Lan"));
    expect((await lan.query(api.voices.mine, {})).hasVoice).toBe(true);

    await lan.mutation(api.notes.create, { ownerId, ...note });
    await settle();
    const [tts] = ttsCalls(calls);
    expect(tts!.body.voice.id).toBe("SIMULATED-lan-voice");
    expect(tts!.body.transcript).toBe("Safety note from Lan: The white bottle on the desk is your medicine water.");
    const [view] = await owner.query(api.notes.forOwner, {});
    expect(view).toMatchObject({ audioStatus: "ready", audioVoice: "helper" });
  });

  it("switches unheard notes to the new voice and gives the paired laptop the same audio", async () => {
    vi.useFakeTimers();
    const calls = cartesia();
    const { t, owner, lan, ownerId, upload, settle } = await family();
    await lan.mutation(api.notes.create, { ownerId, ...note });
    await settle();
    expect(ttsCalls(calls)[0]!.body.voice.id).toBe("SIMULATED-default-voice");
    await owner.action(api.devicesNode.pair, { label: "Demo laptop" }).then(async ({ key }) => {
      await lan.action(api.voicesNode.create, { storageId: await upload(simulatedWav(15_000)), consent: true });
      await settle();
      expect(ttsCalls(calls)[1]!.body.voice.id).toBe("SIMULATED-lan-voice");
      const queue = await t.query(api.notes.forDevice, { deviceKey: key });
      expect(queue!.notes[0]!.audioUrl).toBeTruthy();
      const hits = await t.query(api.notes.forObjectByDevice, { deviceKey: key, label: "water bottle" });
      expect(hits![0]!.audioUrl).toBe(queue!.notes[0]!.audioUrl);
    });
  });

  it("rejects recordings that are not a 10–60 s WAV, and still deletes them", async () => {
    cartesia();
    const { lan, upload, storageCount } = await family();
    for (const [bytes, code] of [[simulatedWav(4_000), "RECORDING_TOO_SHORT"], [simulatedWav(65_000), "RECORDING_TOO_LONG"],
      [new TextEncoder().encode("not audio at all, just text"), "INVALID_RECORDING"]] as const) {
      await expect(lan.action(api.voicesNode.create, { storageId: await upload(bytes), consent: true })).rejects.toThrow(code);
    }
    expect(await storageCount()).toBe(0);
    expect((await lan.query(api.voices.mine, {})).hasVoice).toBe(false);
  });

  it("a provider failure keeps no voice and no recording", async () => {
    cartesia({ clone: () => new Response("bad", { status: 400 }) });
    const { lan, upload, storageCount } = await family();
    await expect(lan.action(api.voicesNode.create, { storageId: await upload(simulatedWav(12_000)), consent: true }))
      .rejects.toThrow("VOICE_CLONE_FAILED");
    expect(await storageCount()).toBe(0);
    expect((await lan.query(api.voices.mine, {})).hasVoice).toBe(false);
  });

  it("withdrawing consent deletes the voice at Cartesia and re-reads every note in the default voice", async () => {
    vi.useFakeTimers();
    const calls = cartesia();
    const { owner, lan, ownerId, upload, settle } = await family();
    await lan.action(api.voicesNode.create, { storageId: await upload(simulatedWav(12_000)), consent: true });
    const noteId = await lan.mutation(api.notes.create, { ownerId, ...note });
    await settle();
    await owner.mutation(api.notes.markHeard, { noteId }); // even heard notes lose the cloned voice
    expect(await lan.action(api.voicesNode.remove, {})).toEqual({ removed: true, rerendered: 1 });
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/voices/SIMULATED-lan-voice"))).toBe(true);
    await settle();
    expect(ttsCalls(calls).at(-1)!.body.voice.id).toBe("SIMULATED-default-voice");
    const [view] = await owner.query(api.notes.forOwner, {});
    expect(view).toMatchObject({ audioStatus: "ready", audioVoice: "default" });
    expect((await lan.query(api.voices.mine, {})).hasVoice).toBe(false);
  });

  it("keeps the voice record when Cartesia could not delete it, so the helper can retry", async () => {
    cartesia({ remove: () => new Response("busy", { status: 503 }) });
    const { lan, upload } = await family();
    await lan.action(api.voicesNode.create, { storageId: await upload(simulatedWav(12_000)), consent: true });
    await expect(lan.action(api.voicesNode.remove, {})).rejects.toThrow("VOICE_DELETE_FAILED");
    expect((await lan.query(api.voices.mine, {})).hasVoice).toBe(true);
  });

  it("falls back to the default voice when Cartesia rejects the helper's voice", async () => {
    vi.useFakeTimers();
    const calls = cartesia({ tts: (body) => body.voice.id === "SIMULATED-lan-voice"
      ? new Response("no such voice", { status: 404 }) : new Response(new Uint8Array(simulatedWav())) });
    const { owner, lan, ownerId, upload, settle } = await family();
    await lan.action(api.voicesNode.create, { storageId: await upload(simulatedWav(12_000)), consent: true });
    await lan.mutation(api.notes.create, { ownerId, ...note });
    await settle();
    expect(ttsCalls(calls).map((c) => c.body.voice.id)).toEqual(["SIMULATED-lan-voice", "SIMULATED-default-voice"]);
    expect((await owner.query(api.notes.forOwner, {}))[0]).toMatchObject({ audioStatus: "ready", audioVoice: "default" });
  });

  it("owner sets speed and volume within Cartesia's ranges, and can turn helper voices off", async () => {
    vi.useFakeTimers();
    const calls = cartesia();
    const { owner, lan, ownerId, upload, settle } = await family();
    expect(await owner.query(api.voices.prefs, {})).toMatchObject({ speed: 1, volume: 1, helperVoices: true });
    await expect(owner.mutation(api.voices.setPrefs, { speed: 2, volume: 1, helperVoices: true })).rejects.toThrow("INVALID_PREFS");
    await expect(owner.mutation(api.voices.setPrefs, { speed: 1, volume: 0.2, helperVoices: true })).rejects.toThrow("INVALID_PREFS");
    await lan.action(api.voicesNode.create, { storageId: await upload(simulatedWav(12_000)), consent: true });
    await lan.mutation(api.notes.create, { ownerId, ...note });
    await settle();
    expect(await owner.mutation(api.voices.setPrefs, { speed: 0.8, volume: 1.4, helperVoices: false })).toBe(1); // unheard note re-rendered
    await settle();
    const last = ttsCalls(calls).at(-1)!;
    expect(last.body.generation_config).toEqual({ speed: 0.8, volume: 1.4 });
    expect(last.body.voice.id).toBe("SIMULATED-default-voice");
  });

  it("a stale note is spoken from text on the laptop, never with audio that leaves out its age", async () => {
    vi.useFakeTimers({ now: 1_000_000, toFake: ["Date"] });
    cartesia();
    const { t, owner, lan, ownerId, settle } = await family();
    const { key } = await owner.action(api.devicesNode.pair, { label: "Demo laptop" });
    await lan.mutation(api.notes.create, { ownerId, ...note, reviewDays: 1 });
    vi.useRealTimers();
    vi.useFakeTimers({ now: 1_000_000 + 2 * 86_400_000, toFake: ["Date"] });
    await settle().catch(() => undefined);
    const queue = await t.query(api.notes.forDevice, { deviceKey: key });
    expect(queue!.notes[0]!.spoken).toContain("may be out of date");
    expect(queue!.notes[0]!.audioUrl).toBeNull();
  });

  it("limits recordings to 3 per helper per hour", async () => {
    cartesia();
    const { lan } = await family();
    for (let i = 0; i < 3; i++) await lan.mutation(api.voices.uploadUrl, {});
    await expect(lan.mutation(api.voices.uploadUrl, {})).rejects.toThrow("VOICE_RATE_LIMITED");
  });

  it("measures WAV duration from the header", () => {
    expect(wavSeconds(simulatedWav(12_000))).toBe(12);
    expect(wavSeconds(new Uint8Array(10))).toBeNull();
  });
});
