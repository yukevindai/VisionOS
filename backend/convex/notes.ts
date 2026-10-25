import { ConvexError, v } from "convex/values";
import { internalMutation, internalQuery, mutation, query } from "./_generated/server";
import type { MutationCtx, QueryCtx } from "./_generated/server";
import type { Doc, Id } from "./_generated/dataModel";
import { internal } from "./_generated/api";
import { requireUser } from "./lib/auth";
import { requireAcceptedFriendship } from "./friends";
import { audioStatus, audioVoice, noteKind } from "./noteTables";
import { runIdValidator, runResultValidator } from "@convex-dev/action-retrier";
import { noteLimiter, noteRetrier } from "./lib/familyComponents";
import { resolveDevice } from "./lib/deviceKeys";
import { labelWords, matchNote, type MatchKind } from "./lib/familyMatch";

// Family notes. An accepted helper leaves a short note for the owner (the VisionOS user):
// "The white bottle is your new blood pressure medicine." The owner hears it later, on the
// web page or on a paired device such as the demo laptop, which speaks notes between tasks.
// Notes are information from a person, never commands: they cannot start guidance,
// confirm a press, clear a hazard or authorize payment (spec S6, S7).
// Access follows the existing friendship: revoking a helper hides their notes at once,
// because every read below re-checks the friendship inside the same query.

export const MAX_NOTE_CHARS = 220; // leaves room for the spoken prefix under Cartesia's 300
export const MAX_PLACE_CHARS = 40;
export const OWNER_LIST_LIMIT = 50;
export const SEARCH_LIMIT = 5;
export const DEVICE_QUEUE_LIMIT = 10;
export const DEFAULT_REVIEW_DAYS = 30;
const DAY_MS = 24 * 60 * 60 * 1000;
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f]/;

function bad(code: string): never {
  throw new ConvexError({ code });
}

function cleanText(raw: string, max: number, code: string): string {
  const text = raw.trim().replace(/\s+/g, " ");
  if (text.length === 0 || text.length > max || CONTROL.test(text)) bad(code);
  return text;
}

type Spoken = { stale?: boolean; ageDays?: number; match?: MatchKind };

/** What the owner hears. The author always comes first, so the source is never ambiguous. */
export function spokenText(kind: "info" | "warning", authorName: string, text: string, opts: Spoken = {}): string {
  const name = authorName.trim().slice(0, 40) || "a helper";
  const days = opts.ageDays ?? 0;
  const age = opts.stale ? `, ${days} ${days === 1 ? "day" : "days"} old, may be out of date` : "";
  if (kind === "warning") return `Safety note from ${name}${age}: ${text}`;
  // Reassurance found by matching an object is never stated as fact.
  if (opts.match === "reassurance") return `According to ${name}'s note${age}: ${text}`;
  return `Note from ${name}${age}: ${text}`;
}

export function isStale(note: Pick<Doc<"helperNotes">, "reviewAt" | "reviewFlagged">, now: number): boolean {
  return note.reviewFlagged === true || (note.reviewAt !== undefined && note.reviewAt <= now);
}

async function acceptedHelperIds(ctx: QueryCtx | MutationCtx, ownerId: Id<"users">): Promise<Set<Id<"users">>> {
  const rows = await ctx.db.query("friendships")
    .withIndex("by_owner_state", (q) => q.eq("ownerId", ownerId).eq("state", "accepted"))
    .collect();
  return new Set(rows.map((row) => row.helperId));
}

async function authorName(ctx: QueryCtx, note: Doc<"helperNotes">): Promise<string> {
  return (await ctx.db.get(note.authorId))?.displayName ?? "";
}

function spokenFor(note: Doc<"helperNotes">, name: string, now: number, match?: MatchKind): string {
  const stale = isStale(note, now);
  return spokenText(note.kind, name, note.text, { stale, ageDays: Math.floor((now - note.createdAt) / DAY_MS), match });
}

/**
 * The rendered WAV says exactly spokenText(kind, author, text). A device may play it only when
 * that is what it would say now: not for a stale note ("may be out of date") or a reassurance
 * match ("According to …"), which are spoken from text instead.
 */
async function deviceAudioUrl(ctx: QueryCtx, note: Doc<"helperNotes">, name: string, spoken: string) {
  if (note.audioStatus !== "ready" || !note.audioStorageId) return null;
  if (spoken !== spokenText(note.kind, name, note.text)) return null;
  return ctx.storage.getUrl(note.audioStorageId);
}

/** Starts (or restarts) the durable Cartesia render of one note. Old audio is deleted first. */
export async function startRender(ctx: MutationCtx, note: Doc<"helperNotes">) {
  if (note.audioStorageId) await ctx.storage.delete(note.audioStorageId);
  const runId = await noteRetrier.run(ctx, internal.notesAudio.render, { noteId: note._id },
    { onComplete: internal.notes.renderFinished });
  await ctx.db.patch(note._id, { audioStatus: "pending", audioStorageId: undefined, audioVoice: undefined,
    audioError: undefined, renderRunId: runId });
}

async function ownerView(ctx: QueryCtx, note: Doc<"helperNotes">, now: number) {
  const name = await authorName(ctx, note);
  return {
    noteId: note._id,
    authorName: name || "a helper",
    kind: note.kind,
    text: note.text,
    place: note.place ?? null,
    spoken: spokenFor(note, name, now),
    stale: isStale(note, now),
    createdAt: note.createdAt,
    heardAt: note.heardAt ?? null,
    audioStatus: note.audioStatus,
    audioVoice: note.audioVoice ?? null,
    audioUrl: note.audioStorageId ? await ctx.storage.getUrl(note.audioStorageId) : null,
  };
}

async function visibleNotes(ctx: QueryCtx, ownerId: Id<"users">, limit: number): Promise<Doc<"helperNotes">[]> {
  const helpers = await acceptedHelperIds(ctx, ownerId);
  const notes = await ctx.db.query("helperNotes")
    .withIndex("by_owner_created", (q) => q.eq("ownerId", ownerId))
    .order("desc").take(limit);
  return notes.filter((n) => helpers.has(n.authorId));
}

/** Notes relevant to an object the user just found, warnings first; reassurance needs a stricter match. */
async function notesForObject(ctx: QueryCtx, ownerId: Id<"users">, label: string, place: string | undefined, now: number) {
  const words = labelWords(cleanText(label, 160, "INVALID_LABEL"));
  if (words.length === 0) return [];
  const helpers = await acceptedHelperIds(ctx, ownerId);
  const hits = await ctx.db.query("helperNotes")
    .withSearchIndex("search_text", (q) => q.search("text", words.join(" ")).eq("ownerId", ownerId))
    .take(20);
  const matched: { note: Doc<"helperNotes">; match: MatchKind }[] = [];
  for (const note of hits) {
    if (!helpers.has(note.authorId)) continue;
    const match = matchNote(note, words, place);
    if (match) matched.push({ note, match });
  }
  matched.sort((a, b) => (a.match !== b.match ? (a.match === "warning" ? -1 : 1)
    : b.note.createdAt - a.note.createdAt || b.note._creationTime - a.note._creationTime));
  return Promise.all(matched.slice(0, SEARCH_LIMIT).map(async ({ note, match }) => {
    const name = await authorName(ctx, note);
    const spoken = spokenFor(note, name, now, match);
    return { noteId: note._id, kind: note.kind, match, heard: note.heardAt !== undefined, spoken,
      audioUrl: await deviceAudioUrl(ctx, note, name, spoken) };
  }));
}

// ---------- helper (writer) ----------

/** Helper writes a note for an owner who accepted them. Audio is rendered in the background. */
export const create = mutation({
  args: { ownerId: v.id("users"), text: v.string(), kind: noteKind, place: v.optional(v.string()),
    reviewDays: v.optional(v.number()) },
  handler: async (ctx, args) => {
    const helper = await requireUser(ctx);
    await requireAcceptedFriendship(ctx, args.ownerId, helper._id);
    const text = cleanText(args.text, MAX_NOTE_CHARS, "INVALID_NOTE");
    const place = args.place === undefined ? undefined : cleanText(args.place, MAX_PLACE_CHARS, "INVALID_PLACE");
    const reviewDays = args.reviewDays ?? DEFAULT_REVIEW_DAYS;
    if (!Number.isInteger(reviewDays) || reviewDays < 1 || reviewDays > 365) bad("INVALID_REVIEW_DAYS");
    const limit = await noteLimiter.limit(ctx, "familyNote", { key: helper._id });
    if (!limit.ok) throw new ConvexError({ code: "NOTE_RATE_LIMITED", retryAfterMs: limit.retryAfter });
    const now = Date.now();
    const reviewAt = now + reviewDays * DAY_MS;
    const noteId = await ctx.db.insert("helperNotes", {
      ownerId: args.ownerId, authorId: helper._id, text, kind: args.kind, place, createdAt: now,
      audioStatus: "pending", reviewAt,
    });
    // Durable, retried render (action-retrier component). onComplete runs exactly once.
    await startRender(ctx, (await ctx.db.get(noteId))!);
    await ctx.scheduler.runAt(reviewAt, internal.notes.flagForReview, { noteId });
    return noteId;
  },
});

/** Helper's live view of the notes they left for one owner: heard receipts and review flags. */
export const byAuthor = query({
  args: { ownerId: v.id("users") },
  handler: async (ctx, { ownerId }) => {
    const helper = await requireUser(ctx);
    await requireAcceptedFriendship(ctx, ownerId, helper._id);
    const now = Date.now();
    const notes = await ctx.db.query("helperNotes")
      .withIndex("by_owner_author", (q) => q.eq("ownerId", ownerId).eq("authorId", helper._id))
      .collect();
    return notes.sort((a, b) => b.createdAt - a.createdAt || b._creationTime - a._creationTime).map((n) => ({
      noteId: n._id, kind: n.kind, text: n.text, place: n.place ?? null, createdAt: n.createdAt,
      heardAt: n.heardAt ?? null, audioStatus: n.audioStatus, audioVoice: n.audioVoice ?? null,
      needsReview: isStale(n, now), reviewAt: n.reviewAt ?? null,
    }));
  },
});

/** Helper confirms a note is still correct; it stops sounding out of date for another period. */
export const confirmStillCorrect = mutation({
  args: { noteId: v.id("helperNotes"), reviewDays: v.optional(v.number()) },
  handler: async (ctx, { noteId, reviewDays }) => {
    const helper = await requireUser(ctx);
    const note = await ctx.db.get(noteId);
    if (!note || note.authorId !== helper._id) bad("NOTE_NOT_FOUND");
    await requireAcceptedFriendship(ctx, note.ownerId, helper._id);
    const days = reviewDays ?? DEFAULT_REVIEW_DAYS;
    if (!Number.isInteger(days) || days < 1 || days > 365) bad("INVALID_REVIEW_DAYS");
    const reviewAt = Date.now() + days * DAY_MS;
    await ctx.db.patch(noteId, { reviewAt, reviewFlagged: false });
    await ctx.scheduler.runAt(reviewAt, internal.notes.flagForReview, { noteId });
    return reviewAt;
  },
});

// ---------- owner (the VisionOS user), signed in ----------

/** Owner's live list, newest first. Notes from revoked helpers disappear on the next update. */
export const forOwner = query({
  args: {},
  handler: async (ctx) => {
    const owner = await requireUser(ctx);
    const now = Date.now();
    return Promise.all((await visibleNotes(ctx, owner._id, OWNER_LIST_LIMIT)).map((n) => ownerView(ctx, n, now)));
  },
});

/** "What did Lan say about the kitchen?" Full-text search over this owner's notes only. */
export const search = query({
  args: { query: v.string() },
  handler: async (ctx, args) => {
    const owner = await requireUser(ctx);
    const text = cleanText(args.query, 200, "INVALID_QUERY");
    const helpers = await acceptedHelperIds(ctx, owner._id);
    const now = Date.now();
    const hits = await ctx.db.query("helperNotes")
      .withSearchIndex("search_text", (q) => q.search("text", text).eq("ownerId", owner._id))
      .take(SEARCH_LIMIT * 2);
    return Promise.all(hits.filter((n) => helpers.has(n.authorId)).slice(0, SEARCH_LIMIT).map((n) => ownerView(ctx, n, now)));
  },
});

/** Notes about an object the user just found (warnings match easily, reassurance strictly). */
export const forObject = query({
  args: { label: v.string(), place: v.optional(v.string()) },
  handler: async (ctx, { label, place }) => {
    const owner = await requireUser(ctx);
    return notesForObject(ctx, owner._id, label, place, Date.now());
  },
});

/** Owner marks a note as heard; the helper's live view shows it immediately. */
export const markHeard = mutation({
  args: { noteId: v.id("helperNotes") },
  handler: async (ctx, { noteId }) => {
    const owner = await requireUser(ctx);
    return markHeardFor(ctx, owner._id, noteId);
  },
});

/** The owner or the author can delete a note; its audio is deleted with it. */
export const remove = mutation({
  args: { noteId: v.id("helperNotes") },
  handler: async (ctx, { noteId }) => {
    const user = await requireUser(ctx);
    const note = await ctx.db.get(noteId);
    if (!note || (note.ownerId !== user._id && note.authorId !== user._id)) bad("NOTE_NOT_FOUND");
    if (note.audioStorageId) await ctx.storage.delete(note.audioStorageId);
    await ctx.db.delete(noteId);
    return true;
  },
});

async function markHeardFor(ctx: MutationCtx, ownerId: Id<"users">, noteId: Id<"helperNotes">) {
  const note = await ctx.db.get(noteId);
  if (!note || note.ownerId !== ownerId || !(await acceptedHelperIds(ctx, ownerId)).has(note.authorId)) bad("NOTE_NOT_FOUND");
  if (note.heardAt === undefined) await ctx.db.patch(noteId, { heardAt: Date.now() });
  return true;
}

// ---------- paired device (the demo laptop), no Auth0 session ----------

/** Live queue for a paired device: unheard notes, oldest first. Null when the key is unknown or revoked. */
export const forDevice = query({
  args: { deviceKey: v.string() },
  handler: async (ctx, { deviceKey }) => {
    const device = await resolveDevice(ctx, deviceKey);
    if (!device) return null;
    const now = Date.now();
    // visibleNotes is newest first by (createdAt, _creationTime); reversing keeps a strict order
    // even when two notes share a millisecond.
    const unheard = (await visibleNotes(ctx, device.ownerId, OWNER_LIST_LIMIT)).filter((n) => n.heardAt === undefined)
      .reverse().slice(0, DEVICE_QUEUE_LIMIT);
    return {
      deviceLabel: device.label,
      notes: await Promise.all(unheard.map(async (n) => {
        const name = await authorName(ctx, n);
        const spoken = spokenFor(n, name, now);
        // audioUrl: the note in the helper's own voice when it has one, so the laptop plays the same audio.
        return { noteId: n._id, kind: n.kind, createdAt: n.createdAt, spoken,
          audioUrl: await deviceAudioUrl(ctx, n, name, spoken) };
      })),
    };
  },
});

export const forObjectByDevice = query({
  args: { deviceKey: v.string(), label: v.string(), place: v.optional(v.string()) },
  handler: async (ctx, { deviceKey, label, place }) => {
    const device = await resolveDevice(ctx, deviceKey);
    return device ? notesForObject(ctx, device.ownerId, label, place, Date.now()) : null;
  },
});

export const markHeardByDevice = mutation({
  args: { deviceKey: v.string(), noteId: v.id("helperNotes") },
  handler: async (ctx, { deviceKey, noteId }) => {
    const device = await resolveDevice(ctx, deviceKey);
    if (!device) bad("DEVICE_NOT_PAIRED");
    return markHeardFor(ctx, device.ownerId, noteId);
  },
});

// ---------- internal ----------

/** Scheduled at reviewAt. Flags the note so every live view updates; ignores stale schedules. */
export const flagForReview = internalMutation({
  args: { noteId: v.id("helperNotes") },
  handler: async (ctx, { noteId }) => {
    const note = await ctx.db.get(noteId);
    if (!note || note.reviewAt === undefined || note.reviewAt > Date.now() || note.reviewFlagged) return false;
    await ctx.db.patch(noteId, { reviewFlagged: true });
    return true;
  },
});

export const getForRender = internalQuery({
  args: { noteId: v.id("helperNotes") },
  handler: async (ctx, { noteId }) => {
    const note = await ctx.db.get(noteId);
    if (!note) return null;
    const author = await ctx.db.get(note.authorId);
    const prefs = await ctx.db.query("familyPrefs").withIndex("by_owner", (q) => q.eq("ownerId", note.ownerId)).unique();
    // The helper's own voice only while it exists (consent not withdrawn) and the owner allows helper voices.
    const voice = prefs?.helperVoices === false ? null
      : await ctx.db.query("helperVoices").withIndex("by_user", (q) => q.eq("userId", note.authorId)).unique();
    return { spoken: spokenText(note.kind, author?.displayName ?? "", note.text), audioStatus: note.audioStatus,
      voiceId: voice?.cartesiaVoiceId ?? null, speed: prefs?.speed ?? null, volume: prefs?.volume ?? null };
  },
});

export const setAudio = internalMutation({
  args: { noteId: v.id("helperNotes"), status: audioStatus, storageId: v.optional(v.id("_storage")),
    error: v.optional(v.string()), voice: v.optional(audioVoice), voiceId: v.optional(v.string()) },
  handler: async (ctx, { noteId, status, storageId, error, voice, voiceId }) => {
    const note = await ctx.db.get(noteId);
    const drop = async () => { // never leave an orphaned blob behind
      if (storageId) await ctx.storage.delete(storageId);
      return false;
    };
    if (!note) return drop(); // deleted while rendering
    if (voice === "helper") {
      // The helper withdrew (or replaced) their voice while this rendered: a re-render is already running.
      const row = await ctx.db.query("helperVoices").withIndex("by_user", (q) => q.eq("userId", note.authorId)).unique();
      if (!row || row.cartesiaVoiceId !== voiceId) return drop();
    }
    if (note.audioStorageId && note.audioStorageId !== storageId) await ctx.storage.delete(note.audioStorageId);
    await ctx.db.patch(noteId, { audioStatus: status, audioStorageId: storageId, audioError: error, audioVoice: voice });
    return true;
  },
});

/** Retrier callback: after the last retry fails, the note stays readable as text (device voice). */
export const renderFinished = internalMutation({
  args: { runId: runIdValidator, result: runResultValidator },
  handler: async (ctx, { runId, result }) => {
    if (result.type === "success") return;
    const note = await ctx.db.query("helperNotes").withIndex("by_render_run", (q) => q.eq("renderRunId", runId)).unique();
    if (!note || note.audioStatus === "ready") return;
    await ctx.db.patch(note._id, { audioStatus: "failed",
      audioError: result.type === "failed" ? "RENDER_FAILED_AFTER_RETRIES" : "RENDER_CANCELED" });
  },
});
