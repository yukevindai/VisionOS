import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import type { Id } from "../convex/_generated/dataModel";
import schema from "../convex/schema";
import { simulatedWav } from "../convex/lib/cartesia";
import { spokenText } from "../convex/notes";
import { isRetryable } from "../convex/notesAudio";
import rateLimiter from "@convex-dev/rate-limiter/test";
import actionRetrier from "@convex-dev/action-retrier/test";
import { labelWords, matchNote } from "../convex/lib/familyMatch";
import { sha256Hex } from "../convex/lib/deviceKeys";

// SIMULATED: Cartesia is a stubbed fetch; no test spends credits.
const modules = import.meta.glob("../convex/**/*.ts");
const ISSUER = "https://simulated-auth.invalid/";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers(); });

function cartesia(response: () => Response = () => new Response(new Uint8Array(simulatedWav()))) {
  vi.stubEnv("CARTESIA_API_KEY", "SIMULATED-key");
  vi.stubEnv("CARTESIA_VOICE_ID", "SIMULATED-voice");
  vi.stubEnv("CARTESIA_MODEL", "SIMULATED-model");
  const fetch = vi.fn(async () => response());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

async function family() {
  const t = convexTest(schema, modules);
  rateLimiter.register(t, "rateLimiter");
  actionRetrier.register(t, "actionRetrier");
  const as = (subject: string, name: string) => t.withIdentity({ issuer: ISSUER, subject, name });
  const owner = as("minh", "Minh"), lan = as("lan", "Lan"), stranger = as("stranger", "Stranger");
  for (const user of [owner, lan, stranger]) await user.mutation(api.users.ensure, {});
  const id = (subject: string) => t.run(async (ctx) => (await ctx.db.query("users")
    .withIndex("by_auth", (q) => q.eq("issuer", ISSUER).eq("subject", subject)).unique())!._id);
  const ownerId = await id("minh"), lanId = await id("lan");
  const friendshipId = await t.run((ctx) => ctx.db.insert("friendships", {
    ownerId, helperId: lanId, state: "accepted", notify: false, createdAt: 1, updatedAt: 1 }));
  const revoke = () => t.run((ctx) => ctx.db.patch(friendshipId, { state: "revoked" }));
  return { t, owner, lan, stranger, ownerId: ownerId as Id<"users">, revoke };
}

const medicine = { text: "The white bottle is your new blood pressure medicine.", kind: "info" as const, place: "kitchen" };

describe("family notes", () => {
  it("only an accepted helper can leave a note", async () => {
    cartesia();
    const { owner, stranger, ownerId } = await family();
    await expect(stranger.mutation(api.notes.create, { ownerId, ...medicine })).rejects.toThrow("Friend access unavailable");
    expect(await owner.query(api.notes.forOwner, {})).toEqual([]);
  });

  it("renders audio once in the background and says who wrote it", async () => {
    vi.useFakeTimers();
    const fetch = cartesia();
    const { t, owner, lan, ownerId } = await family();
    await lan.mutation(api.notes.create, { ownerId, ...medicine });
    let [note] = await owner.query(api.notes.forOwner, {});
    expect(note).toMatchObject({ authorName: "Lan", audioStatus: "pending", audioUrl: null, heardAt: null });
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    [note] = await owner.query(api.notes.forOwner, {});
    expect(note!.audioStatus).toBe("ready");
    expect(note!.audioUrl).toBeTruthy();
    expect(note!.spoken).toBe("Note from Lan: The white bottle is your new blood pressure medicine.");
    expect(fetch).toHaveBeenCalledTimes(1);
    const body = JSON.parse((fetch.mock.calls[0] as unknown as [string, { body: string }])[1].body);
    expect(body.transcript).toBe(note!.spoken);
  });

  it("labels warnings so they are never mistaken for reassurance", () => {
    expect(spokenText("warning", "Lan", "The second drawer has knives.")).toBe("Safety note from Lan: The second drawer has knives.");
    expect(spokenText("info", "  ", "Hi")).toBe("Note from a helper: Hi");
  });

  it("retries a temporary provider failure, then keeps the note readable as text", async () => {
    vi.useFakeTimers();
    const fetch = cartesia(() => new Response("busy", { status: 503 }));
    const { t, owner, lan, ownerId } = await family();
    await lan.mutation(api.notes.create, { ownerId, ...medicine });
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const [note] = await owner.query(api.notes.forOwner, {});
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(note).toMatchObject({ audioStatus: "failed", audioUrl: null, text: medicine.text });
  });

  it("does not retry errors a retry cannot fix (bad key, no credits)", async () => {
    vi.useFakeTimers();
    const fetch = cartesia(() => new Response("quota", { status: 402 }));
    const { t, owner, lan, ownerId } = await family();
    await lan.mutation(api.notes.create, { ownerId, ...medicine });
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const [note] = await owner.query(api.notes.forOwner, {});
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(note).toMatchObject({ audioStatus: "failed", text: medicine.text });
    expect(isRetryable("PROVIDER_ERROR", 503)).toBe(true);
    expect(isRetryable("PROVIDER_ERROR", 429)).toBe(true);
    expect(isRetryable("PROVIDER_ERROR", 401)).toBe(false);
    expect(isRetryable("INVALID_PROVIDER_AUDIO")).toBe(false);
  });

  it("does not call Cartesia when speech is not configured", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const { t, owner, lan, ownerId } = await family();
    await lan.mutation(api.notes.create, { ownerId, ...medicine });
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const [note] = await owner.query(api.notes.forOwner, {});
    expect(note!.audioStatus).toBe("failed");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("closes the loop: the helper sees when the owner heard the note", async () => {
    cartesia();
    const { owner, lan, stranger, ownerId } = await family();
    const noteId = await lan.mutation(api.notes.create, { ownerId, ...medicine });
    expect((await lan.query(api.notes.byAuthor, { ownerId }))[0]!.heardAt).toBeNull();
    await expect(stranger.mutation(api.notes.markHeard, { noteId })).rejects.toThrow("NOTE_NOT_FOUND");
    await owner.mutation(api.notes.markHeard, { noteId });
    expect((await lan.query(api.notes.byAuthor, { ownerId }))[0]!.heardAt).toEqual(expect.any(Number));
  });

  it("revoking a helper hides their notes and blocks new ones immediately", async () => {
    cartesia();
    const { owner, lan, ownerId, revoke } = await family();
    const noteId = await lan.mutation(api.notes.create, { ownerId, ...medicine });
    expect(await owner.query(api.notes.forOwner, {})).toHaveLength(1);
    await revoke();
    expect(await owner.query(api.notes.forOwner, {})).toEqual([]);
    expect(await owner.query(api.notes.search, { query: "medicine" })).toEqual([]);
    await expect(owner.mutation(api.notes.markHeard, { noteId })).rejects.toThrow("NOTE_NOT_FOUND");
    await expect(lan.mutation(api.notes.create, { ownerId, ...medicine })).rejects.toThrow("Friend access unavailable");
  });

  it("search covers only this owner's notes", async () => {
    cartesia();
    const { owner, lan, ownerId } = await family();
    await lan.mutation(api.notes.create, { ownerId, ...medicine });
    await lan.mutation(api.notes.create, { ownerId, text: "The second drawer has knives.", kind: "warning", place: "kitchen" });
    const hits = await owner.query(api.notes.search, { query: "medicine" });
    expect(hits.map((h: { text: string }) => h.text)).toEqual([medicine.text]);
    expect(await lan.query(api.notes.search, { query: "medicine" })).toEqual([]);
  });

  it("validates text and rate-limits a helper", async () => {
    cartesia();
    const { lan, ownerId } = await family();
    for (const text of ["   ", "x".repeat(221), "bad\u0007bell"]) {
      await expect(lan.mutation(api.notes.create, { ownerId, text, kind: "info" })).rejects.toThrow("INVALID_NOTE");
    }
    // Burst of 5 (rate-limiter component token bucket); the 6th in a row is refused.
    for (let i = 0; i < 5; i++) await lan.mutation(api.notes.create, { ownerId, text: `Note ${i}`, kind: "info" });
    await expect(lan.mutation(api.notes.create, { ownerId, text: "One more", kind: "info" })).rejects.toThrow("NOTE_RATE_LIMITED");
  });

  it("deleting a note deletes its audio too", async () => {
    vi.useFakeTimers();
    cartesia();
    const { t, owner, lan, ownerId } = await family();
    const noteId = await lan.mutation(api.notes.create, { ownerId, ...medicine });
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    const storageId = await t.run(async (ctx) => (await ctx.db.get(noteId))!.audioStorageId!);
    await owner.mutation(api.notes.remove, { noteId });
    expect(await t.run((ctx) => ctx.storage.get(storageId))).toBeNull();
    expect(await owner.query(api.notes.forOwner, {})).toEqual([]);
  });

  it("only the owner can pair a device; the key is shown once and stored as a hash", async () => {
    const { t, owner, lan } = await family();
    await expect(t.action(api.devicesNode.pair, { label: "Laptop" })).rejects.toThrow("Authentication required");
    const { deviceId, key } = await owner.action(api.devicesNode.pair, { label: "Demo laptop" });
    expect(key).toMatch(/^vfn_[A-Za-z0-9_-]{43}$/);
    const stored = await t.run((ctx) => ctx.db.get(deviceId));
    expect(stored!.keyHash).toBe(await sha256Hex(key));
    expect(JSON.stringify(stored)).not.toContain(key);
    expect(await owner.query(api.devices.list, {})).toEqual([expect.objectContaining({ label: "Demo laptop" })]);
    expect(await lan.query(api.devices.list, {})).toEqual([]);
  });

  it("a paired laptop gets unheard notes live, oldest first, and can mark them heard", async () => {
    cartesia();
    const { t, owner, lan, ownerId } = await family();
    const { key } = await owner.action(api.devicesNode.pair, { label: "Demo laptop" });
    expect(await t.query(api.notes.forDevice, { deviceKey: key })).toEqual({ deviceLabel: "Demo laptop", notes: [] });
    const first = await lan.mutation(api.notes.create, { ownerId, ...medicine });
    await lan.mutation(api.notes.create, { ownerId, text: "Dinner is in the fridge.", kind: "info" });
    const queue = await t.query(api.notes.forDevice, { deviceKey: key });
    expect(queue!.notes.map((n) => n.spoken)).toEqual([
      "Note from Lan: The white bottle is your new blood pressure medicine.", "Note from Lan: Dinner is in the fridge."]);
    await t.mutation(api.notes.markHeardByDevice, { deviceKey: key, noteId: first });
    expect((await t.query(api.notes.forDevice, { deviceKey: key }))!.notes).toHaveLength(1);
    expect((await lan.query(api.notes.byAuthor, { ownerId })).find((n) => n.noteId === first)!.heardAt).toEqual(expect.any(Number));
  });

  it("an unknown or revoked device key reads nothing and cannot mark notes", async () => {
    cartesia();
    const { t, owner, lan, ownerId } = await family();
    const { deviceId, key } = await owner.action(api.devicesNode.pair, { label: "Demo laptop" });
    const noteId = await lan.mutation(api.notes.create, { ownerId, ...medicine });
    expect(await t.query(api.notes.forDevice, { deviceKey: "vfn_" + "A".repeat(43) })).toBeNull();
    expect(await t.query(api.notes.forDevice, { deviceKey: "not-a-key" })).toBeNull();
    await owner.mutation(api.devices.revoke, { deviceId });
    expect(await t.query(api.notes.forDevice, { deviceKey: key })).toBeNull();
    expect(await t.query(api.notes.forObjectByDevice, { deviceKey: key, label: "white bottle" })).toBeNull();
    await expect(t.mutation(api.notes.markHeardByDevice, { deviceKey: key, noteId })).rejects.toThrow("DEVICE_NOT_PAIRED");
  });

  it("matches warnings easily and reassurance only on every word in the same place", () => {
    const words = labelWords("Blue bottle (cleaner)");
    expect(words).toEqual(["blue", "bottle"]);
    const bleach = { kind: "warning" as const, text: "The bottle under the sink is bleach.", place: "bathroom" };
    const candy = { kind: "info" as const, text: "The blue bottle is candy.", place: "kitchen" };
    expect(matchNote(bleach, words)).toBe("warning"); // one shared word is enough to warn
    expect(matchNote(candy, words, "kitchen")).toBe("reassurance");
    expect(matchNote(candy, words, "bathroom")).toBeNull(); // wrong room: never reassure
    expect(matchNote(candy, words)).toBeNull(); // unknown room: never reassure
    expect(matchNote(candy, labelWords("white bottle"), "kitchen")).toBeNull(); // not every word
    expect(matchNote(candy, labelWords("the a it"), "kitchen")).toBeNull();
  });

  it("object lookup speaks warnings first and never states reassurance as fact", async () => {
    cartesia();
    const { owner, lan, ownerId } = await family();
    await lan.mutation(api.notes.create, { ownerId, text: "The blue bottle is candy.", kind: "info", place: "kitchen" });
    await lan.mutation(api.notes.create, { ownerId, text: "The bottle under the sink is bleach.", kind: "warning" });
    const hits = await owner.query(api.notes.forObject, { label: "blue bottle", place: "kitchen" });
    expect(hits.map((h) => h.spoken)).toEqual([
      "Safety note from Lan: The bottle under the sink is bleach.",
      "According to Lan's note: The blue bottle is candy."]);
    const elsewhere = await owner.query(api.notes.forObject, { label: "blue bottle", place: "garage" });
    expect(elsewhere.map((h) => h.match)).toEqual(["warning"]);
  });

  it("flags notes for review on schedule and lets the helper confirm them", async () => {
    vi.useFakeTimers();
    cartesia();
    const { t, owner, lan, ownerId } = await family();
    const noteId = await lan.mutation(api.notes.create, { ownerId, ...medicine, reviewDays: 1 });
    await expect(lan.mutation(api.notes.create, { ownerId, ...medicine, reviewDays: 0 })).rejects.toThrow("INVALID_REVIEW_DAYS");
    expect((await lan.query(api.notes.byAuthor, { ownerId }))[0]!.needsReview).toBe(false);
    vi.advanceTimersByTime(25 * 60 * 60 * 1000);
    await t.finishAllScheduledFunctions(vi.runAllTimers);
    expect((await lan.query(api.notes.byAuthor, { ownerId }))[0]!.needsReview).toBe(true);
    const [note] = await owner.query(api.notes.forOwner, {});
    expect(note!.spoken).toBe("Note from Lan, 1 day old, may be out of date: The white bottle is your new blood pressure medicine.");
    await lan.mutation(api.notes.confirmStillCorrect, { noteId });
    expect((await lan.query(api.notes.byAuthor, { ownerId }))[0]!.needsReview).toBe(false);
    expect((await owner.query(api.notes.forOwner, {}))[0]!.stale).toBe(false);
  });
});
