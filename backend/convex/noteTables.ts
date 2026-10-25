import { defineTable } from "convex/server";
import { v } from "convex/values";

/** Family notes: an accepted helper leaves a short note for the owner; the owner hears it later. */
export const noteKind = v.union(v.literal("info"), v.literal("warning"));
export const audioStatus = v.union(v.literal("pending"), v.literal("ready"), v.literal("failed"));

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
};
