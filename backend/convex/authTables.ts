import { defineTable } from "convex/server";
import { v } from "convex/values";

export const authTables = {
  users: defineTable({
    issuer: v.string(),
    subject: v.string(),
    displayName: v.string(),
    automaticSharing: v.boolean(),
    createdAt: v.number(),
    updatedAt: v.number(),
  }).index("by_auth", ["issuer", "subject"]),
  friendInvites: defineTable({
    ownerId: v.id("users"),
    codeHash: v.string(),
    expiresAt: v.number(),
    state: v.union(v.literal("open"), v.literal("pending"), v.literal("accepted"), v.literal("revoked")),
    boundHelperId: v.optional(v.id("users")),
    requestedById: v.optional(v.id("users")),
    createdAt: v.number(),
    redeemedAt: v.optional(v.number()),
    acceptedAt: v.optional(v.number()),
  })
    .index("by_code_hash", ["codeHash"])
    .index("by_owner", ["ownerId"])
    .index("by_owner_state", ["ownerId", "state"])
    .index("by_owner_state_expiry", ["ownerId", "state", "expiresAt"]),
  friendships: defineTable({
    ownerId: v.id("users"),
    helperId: v.id("users"),
    state: v.union(v.literal("pending"), v.literal("accepted"), v.literal("revoked")),
    notify: v.boolean(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_owner_helper", ["ownerId", "helperId"])
    .index("by_helper", ["helperId"])
    .index("by_owner", ["ownerId"])
    .index("by_owner_state", ["ownerId", "state"])
    .index("by_helper_state", ["helperId", "state"]),
};
