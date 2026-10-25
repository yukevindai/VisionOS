import { defineTable } from "convex/server";
import { v } from "convex/values";

/** Family notes: an accepted helper leaves a short note for the owner; the owner hears it later. */
export const noteKind = v.union(v.literal("info"), v.literal("warning"));
export const audioStatus = v.union(v.literal("pending"), v.literal("ready"), v.literal("failed"));
/** Which voice a note's audio was rendered in: the helper's own (cloned, consented) or the default. */
export const audioVoice = v.union(v.literal("helper"), v.literal("default"));

export const noteTables = {
  helperNotes: defineTable({
    ownerId: v.id("users"),
    authorId: v.id("users"),
    text: v.string(),
    kind: noteKind,
    place: v.optional(v.string()),
    createdAt: v.number(),
    audioStatus,
    audioStorageId: v.optional(v.id("_storage")),
    renderRunId: v.optional(v.string()), // action-retrier run that renders this note
    audioError: v.optional(v.string()),
    audioVoice: v.optional(audioVoice),
    heardAt: v.optional(v.number()),
    reviewAt: v.optional(v.number()), // after this, the note is spoken as possibly out of date
    reviewFlagged: v.optional(v.boolean()), // set by a scheduled check so helper views update live
  })
    .index("by_owner_created", ["ownerId", "createdAt"])
    .index("by_author_created", ["authorId", "createdAt"])
    .index("by_owner_author", ["ownerId", "authorId"])
    .index("by_render_run", ["renderRunId"])
    .searchIndex("search_text", { searchField: "text", filterFields: ["ownerId"] }),
  /** A device the owner paired (the demo laptop). It reads notes with a secret key; only its hash is stored. */
  familyDevices: defineTable({
    ownerId: v.id("users"),
    keyHash: v.string(),
    label: v.string(),
    createdAt: v.number(),
    revokedAt: v.optional(v.number()),
  })
    .index("by_key_hash", ["keyHash"])
    .index("by_owner", ["ownerId"]),
  /**
   * A helper's own voice (Cartesia Instant Voice Clone), made from one recording in which they
   * read the consent sentence aloud. Used only to read that helper's notes. The recording itself
   * is deleted right after cloning; deleting this row deletes the voice at Cartesia first.
   */
  helperVoices: defineTable({
    userId: v.id("users"),
    cartesiaVoiceId: v.string(),
    consentText: v.string(),
    consentAt: v.number(),
    recordingSeconds: v.number(),
  }).index("by_user", ["userId"]),
  /** How the owner (the VisionOS user) wants notes to sound. Defaults: 1.0, 1.0, helper voices on. */
  familyPrefs: defineTable({
    ownerId: v.id("users"),
    speed: v.number(),
    volume: v.number(),
    helperVoices: v.boolean(),
    updatedAt: v.number(),
  }).index("by_owner", ["ownerId"]),
};
