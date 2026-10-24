import { defineTable } from "convex/server";
import { v } from "convex/values";

export const mediaTables = {
  // Room names are permanent tombstones, including after provider deletion.
  mediaRooms: defineTable({
    sessionId: v.id("sessions"), epoch: v.string(), generation: v.number(), roomId: v.string(),
    state: v.union(v.literal("provisioning"), v.literal("active"), v.literal("retired"), v.literal("closed")),
    roomSid: v.optional(v.string()), authorizationVersion: v.number(), createdAt: v.number(),
    auditAfter: v.number(), provisionExpiresAt: v.number(), reconcileAttempt: v.number(), reconcileAfter: v.number(),
  }).index("by_name", ["roomId"]).index("by_state_audit", ["state", "auditAfter"]),
  mediaTokens: defineTable({
    roomKey: v.id("mediaRooms"), userId: v.id("users"), identity: v.string(),
    role: v.union(v.literal("owner"), v.literal("helper")), clientInstanceId: v.string(),
    authorizationVersion: v.number(), leaseVersion: v.optional(v.number()),
    attemptId: v.string(), expiresAt: v.number(),
    state: v.union(v.literal("prepared"), v.literal("issued")),
    participantSid: v.optional(v.string()),
  }).index("by_room_identity", ["roomKey", "identity"]).index("by_room", ["roomKey"]),
  mediaEvents: defineTable({
    eventId: v.string(), digest: v.string(), roomId: v.string(), roomSid: v.string(),
    event: v.string(), receivedAt: v.number(),
  }).index("by_event", ["eventId"]),
};
