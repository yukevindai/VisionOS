import { defineTable } from "convex/server";
import { v } from "convex/values";

export const helperSlot = v.union(
  v.object({ kind: v.literal("empty") }),
  v.object({
    kind: v.literal("reserved"), helperId: v.id("users"),
    clientInstanceId: v.string(), connectionId: v.string(), leaseVersion: v.number(),
    reservationAuthorizationVersion: v.number(),
    expiresAt: v.number(),
  }),
  v.object({
    kind: v.literal("connected"), helperId: v.id("users"),
    clientInstanceId: v.string(), connectionId: v.string(), leaseVersion: v.number(),
    reservationAuthorizationVersion: v.number(),
    participantSid: v.optional(v.string()),
    expiresAt: v.number(), authorizationExpiresAt: v.number(),
  }),
);

export const sessionTables = {
  sessions: defineTable({
    ownerId: v.id("users"), epoch: v.string(),
    status: v.union(v.literal("active"), v.literal("ended")),
    sharing: v.boolean(), authorizationVersion: v.number(), nextLeaseVersion: v.number(), nextMediaGeneration: v.number(),
    ownerExpiresAt: v.number(), sharingExpiresAt: v.number(), createdAt: v.number(), endedAt: v.optional(v.number()),
    endReason: v.optional(v.string()),
    media: v.union(v.object({ kind: v.literal("unready") }),
      v.object({ kind: v.literal("provisioning"), roomId: v.string(), generation: v.number() }),
      v.object({ kind: v.literal("retired"), roomId: v.string(), generation: v.number() }),
      v.object({ kind: v.literal("ready"), roomId: v.string(), generation: v.number(), roomSid: v.string(), ownerConnected: v.boolean() })),
    cleanupPending: v.boolean(), slot: helperSlot,
  }).index("by_owner", ["ownerId"]).index("by_owner_status", ["ownerId", "status"])
    .index("by_room", ["media.roomId"])
    .index("by_owner_epoch", ["ownerId", "epoch"]),
  sessionRemovals: defineTable({
    sessionId: v.id("sessions"), helperId: v.id("users"), removed: v.boolean(),
  }).index("by_session_helper", ["sessionId", "helperId"]),
  mediaCleanup: defineTable({
    sessionId: v.id("sessions"), epoch: v.string(), authorizationVersion: v.number(),
    roomKey: v.id("mediaRooms"), attempt: v.number(), nextAttemptAt: v.number(), claimedUntil: v.number(),
    lastFailure: v.optional(v.string()),
    roomId: v.string(), target: v.union(
      v.object({ kind: v.literal("room") }),
      v.object({ kind: v.literal("helper"), connectionId: v.string(), leaseVersion: v.number() }),
    ),
    reason: v.string(), state: v.union(v.literal("pending"), v.literal("verified")),
    createdAt: v.number(), verifiedAt: v.optional(v.number()),
  }).index("by_session_state", ["sessionId", "state"]).index("by_room_state", ["roomKey", "state"]),
};
