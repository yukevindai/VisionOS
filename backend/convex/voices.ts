import { ConvexError, v } from "convex/values";
import { internalMutation, internalQuery, mutation, query } from "./_generated/server";
import type { MutationCtx } from "./_generated/server";
import type { Doc, Id } from "./_generated/dataModel";
import { requireUser } from "./lib/auth";
import { noteLimiter } from "./lib/familyComponents";
import { startRender } from "./notes";
import { SPEED_RANGE, VOLUME_RANGE } from "./lib/speechLimits";

// Family Voice. A helper can let their notes be read in their own voice instead of a stock one.
// Consent is part of the recording: the helper reads CONSENT aloud, in their own voice, and that
// recording (10–60 s, from the microphone, never an uploaded file) is the only input to Cartesia's
// Instant Voice Clone. The recording is deleted right after cloning. The voice is used for nothing
// but that helper's own notes: never for guidance, hazards, system messages or anyone else's notes.
// The helper can delete it at any time; the owner can turn helper voices off. Either way every note
// that used it is re-rendered in the default voice.

export const RERENDER_LIMIT = 50;

/** What the helper reads aloud. It names them, the purpose and the way out. */
export function consentSentence(helperName: string): string {
  const name = helperName.trim().slice(0, 40) || "a VisionOS helper";
  return `My name is ${name}. I agree that VisionOS may make a copy of my voice from this recording, ` +
    `only to read my own notes to my family. I can delete it at any time.`;
}

/** Read after the consent sentence so the recording is long enough to sound like the speaker. */
export const PRACTICE_TEXT = "Here is a little more of how I usually talk. I'll leave notes about where things are, " +
  "what's new around the house, and anything I want you to be careful with. Talk soon.";

function bad(code: string): never {
  throw new ConvexError({ code });
}

async function rerender(ctx: MutationCtx, notes: Doc<"helperNotes">[]) {
  for (const note of notes.slice(0, RERENDER_LIMIT)) await startRender(ctx, note);
  return Math.min(notes.length, RERENDER_LIMIT);
}

// ---------- helper ----------

/** The signed-in helper's voice status and the exact sentence to read. */
export const mine = query({
  args: {},
  handler: async (ctx) => {
    const user = await requireUser(ctx);
    const voice = await ctx.db.query("helperVoices").withIndex("by_user", (q) => q.eq("userId", user._id)).unique();
    return {
      hasVoice: voice !== null,
      consentAt: voice?.consentAt ?? null,
      consentText: voice?.consentText ?? consentSentence(user.displayName ?? ""),
      practiceText: PRACTICE_TEXT,
    };
  },
});

/** One-time upload URL for the consented recording. Rate limited: cloning spends Cartesia credits. */
export const uploadUrl = mutation({
  args: {},
  handler: async (ctx) => {
    const user = await requireUser(ctx);
    const limit = await noteLimiter.limit(ctx, "voiceUpload", { key: user._id });
    if (!limit.ok) throw new ConvexError({ code: "VOICE_RATE_LIMITED", retryAfterMs: limit.retryAfter });
    return ctx.storage.generateUploadUrl();
  },
});

// ---------- owner ----------

/** How notes sound for the signed-in owner. */
export const prefs = query({
  args: {},
  handler: async (ctx) => {
    const owner = await requireUser(ctx);
    const row = await ctx.db.query("familyPrefs").withIndex("by_owner", (q) => q.eq("ownerId", owner._id)).unique();
    return { speed: row?.speed ?? 1, volume: row?.volume ?? 1, helperVoices: row?.helperVoices ?? true,
      speedRange: SPEED_RANGE, volumeRange: VOLUME_RANGE };
  },
});

/** Owner sets speed, volume and whether helpers' own voices are used. Unheard notes re-render at once. */
export const setPrefs = mutation({
  args: { speed: v.number(), volume: v.number(), helperVoices: v.boolean() },
  handler: async (ctx, { speed, volume, helperVoices }) => {
    const owner = await requireUser(ctx);
    const ok = (x: number, [lo, hi]: readonly [number, number]) => Number.isFinite(x) && x >= lo && x <= hi;
    if (!ok(speed, SPEED_RANGE) || !ok(volume, VOLUME_RANGE)) bad("INVALID_PREFS");
    const row = await ctx.db.query("familyPrefs").withIndex("by_owner", (q) => q.eq("ownerId", owner._id)).unique();
    const value = { speed: Math.round(speed * 100) / 100, volume: Math.round(volume * 100) / 100, helperVoices,
      updatedAt: Date.now() };
    if (row) await ctx.db.patch(row._id, value);
    else await ctx.db.insert("familyPrefs", { ownerId: owner._id, ...value });
    const unheard = (await ctx.db.query("helperNotes").withIndex("by_owner_created", (q) => q.eq("ownerId", owner._id))
      .order("desc").take(RERENDER_LIMIT)).filter((n) => n.heardAt === undefined);
    return rerender(ctx, unheard);
  },
});

// ---------- internal (used by voicesNode actions) ----------

export const me = internalQuery({
  args: {},
  handler: async (ctx) => {
    const user = await requireUser(ctx);
    const voice = await ctx.db.query("helperVoices").withIndex("by_user", (q) => q.eq("userId", user._id)).unique();
    return { userId: user._id, displayName: user.displayName ?? "", voiceId: voice?.cartesiaVoiceId ?? null };
  },
});

/** Saves a new voice. Returns false (caller deletes the new clone) if the helper already has one. */
export const save = internalMutation({
  args: { userId: v.id("users"), voiceId: v.string(), consentText: v.string(), recordingSeconds: v.number() },
  handler: async (ctx, args) => {
    const existing = await ctx.db.query("helperVoices").withIndex("by_user", (q) => q.eq("userId", args.userId)).unique();
    if (existing) return false;
    await ctx.db.insert("helperVoices", { userId: args.userId, cartesiaVoiceId: args.voiceId,
      consentText: args.consentText, consentAt: Date.now(), recordingSeconds: args.recordingSeconds });
    // Unheard notes switch to the helper's voice right away.
    const notes = await ctx.db.query("helperNotes").withIndex("by_author_created", (q) => q.eq("authorId", args.userId))
      .order("desc").take(RERENDER_LIMIT);
    await rerender(ctx, notes.filter((n) => n.heardAt === undefined));
    return true;
  },
});

/** After the voice was deleted at Cartesia: forget it and re-render every note that used it. */
export const forget = internalMutation({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    const row = await ctx.db.query("helperVoices").withIndex("by_user", (q) => q.eq("userId", userId)).unique();
    if (row) await ctx.db.delete(row._id);
    const notes = await ctx.db.query("helperNotes").withIndex("by_author_created", (q) => q.eq("authorId", userId))
      .collect();
    return rerender(ctx, notes.filter((n) => n.audioVoice === "helper" || n.audioStatus === "pending"));
  },
});

export const discardUpload = internalMutation({
  args: { storageId: v.id("_storage") },
  handler: async (ctx, { storageId }) => {
    await ctx.storage.delete(storageId);
  },
});

export type VoiceUserId = Id<"users">;
