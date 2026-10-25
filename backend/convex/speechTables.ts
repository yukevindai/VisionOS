import { defineTable } from "convex/server";
import { v } from "convex/values";

// R3 Cartesia cache: one stored WAV per (model, voice, text). Only REAL_API audio
// is cached; SIMULATED output and failures never are. Keys are SHA-256 digests,
// so no transcript text is stored in this table.
export const speechTables = {
  speechCache: defineTable({
    key: v.string(),
    storageId: v.id("_storage"),
    characters: v.number(),
    createdAt: v.number(),
  }).index("by_key", ["key"]),
};
