import { v } from "convex/values";
import { internalMutation, mutation, query, type MutationCtx } from "./_generated/server";
import { requireUser } from "./lib/auth";
import { connectionArgs, fail, helperEligible, invalidate, LEASE_TTL_MS, nextVersion,
  opaque, ownerSession, renewalNonce, requireConnection, requireLive, scheduleSharingExpiry, scheduleSlotExpiry, scopeArgs, sessionArgs, sessionFor } from "./lib/leases";

export const reserveHelper = mutation({
  args: { ...sessionArgs, clientInstanceId: v.string() },
  handler: async (ctx, args) => {
    const helper = await requireUser(ctx);
    const session = await sessionFor(ctx, args);
    const now = Date.now();
    await helperEligible(ctx, session, helper._id, now);
    opaque(args.clientInstanceId);
    if (session.slot.kind !== "empty") {
      if (session.slot.kind === "reserved" && session.slot.helperId === helper._id &&
          session.slot.clientInstanceId === args.clientInstanceId && session.slot.expiresAt > now) return session.slot;
      fail("HELPER_SLOT_OCCUPIED");
    }
    const leaseVersion = nextVersion(session.nextLeaseVersion);
    const slot = {
      kind: "reserved" as const, helperId: helper._id, clientInstanceId: args.clientInstanceId,
      connectionId: `helper:${session._id}:${leaseVersion}`, leaseVersion,
      reservationAuthorizationVersion: session.authorizationVersion,
      expiresAt: Math.min(now + LEASE_TTL_MS, session.ownerExpiresAt, session.sharingExpiresAt),
    };
    await ctx.db.patch(session._id, { slot, nextLeaseVersion: leaseVersion });
    await scheduleSlotExpiry(ctx, { ...session, slot });
    return slot;
  },
});

// Viewer bootstrap and heartbeat use a fresh nonce to avoid cached lease snapshots.
export const helperStatus = query({
  args: { ...connectionArgs, nonce: v.string() },
  handler: async (ctx, args) => {
    const helper = await requireUser(ctx);
    renewalNonce(args.nonce);
    const closed = { state: "closed" as const, nonce: args.nonce, validForMs: 0 };
    const session = await ctx.db.get(args.sessionId);
    const now = Date.now();
    if (!session || session.epoch !== args.epoch || session.ownerId === helper._id ||
        session.status !== "active" || session.ownerExpiresAt <= now || !session.sharing ||
        session.sharingExpiresAt <= now || session.cleanupPending ||
        session.media.kind !== "ready" || !session.media.ownerConnected) return closed;
    const media = session.media;
    const slot = session.slot;
    if (slot.kind === "empty" || slot.helperId !== helper._id || slot.connectionId !== args.connectionId ||
        slot.leaseVersion !== args.leaseVersion || slot.expiresAt <= now ||
        (slot.kind === "connected" && (slot.authorizationExpiresAt <= now || !slot.participantSid))) return closed;
    const grant = await ctx.db.query("friendships").withIndex("by_owner_helper", q =>
      q.eq("ownerId", session.ownerId).eq("helperId", helper._id)).unique();
    const removal = await ctx.db.query("sessionRemovals").withIndex("by_session_helper", q =>
      q.eq("sessionId", session._id).eq("helperId", helper._id)).unique();
    if (grant?.state !== "accepted" || removal?.removed) return closed;
    const room = await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", media.roomId)).unique();
    if (!room || room.state !== "active" || room.sessionId !== session._id || room.epoch !== session.epoch ||
        room.generation !== media.generation || room.roomSid !== media.roomSid) return closed;
    const ownerIdentity = `owner:${room._id}`;
    const owner = await ctx.db.query("mediaTokens").withIndex("by_room_identity", q =>
      q.eq("roomKey", room._id).eq("identity", ownerIdentity)).unique();
    if (!owner || owner.state !== "issued" || owner.role !== "owner" || owner.userId !== session.ownerId || !owner.participantSid) return closed;
    const validForMs = Math.min(LEASE_TTL_MS, session.ownerExpiresAt - now, session.sharingExpiresAt - now,
      slot.expiresAt - now, slot.kind === "connected" ? slot.authorizationExpiresAt - now : LEASE_TTL_MS);
    const status = { nonce: args.nonce, validForMs, authorizationVersion: session.authorizationVersion,
      roomName: room.roomId, roomSid: room.roomSid, generation: room.generation,
      ownerIdentity, ownerParticipantSid: owner.participantSid };
    return slot.kind === "reserved" ? { ...status, state: "reserved" as const }
      : { ...status, state: "connected" as const, participantSid: slot.participantSid };
  },
});

// Only the provider reconciliation path supplies this verified membership.
export async function confirmVerifiedConnection(ctx: MutationCtx, args: {
  sessionId: import("./_generated/dataModel").Id<"sessions">; epoch: string;
  connectionId: string; leaseVersion: number; authorizationVersion: number;
  roomId: string; roomSid: string; participantSid: string;
}) {
    const session = await sessionFor(ctx, args);
    const now = Date.now();
    if (session.media.kind !== "ready" || session.media.roomId !== args.roomId || session.media.roomSid !== args.roomSid ||
        (session.authorizationVersion !== args.authorizationVersion && !(session.slot.kind === "connected" &&
          session.slot.reservationAuthorizationVersion === args.authorizationVersion &&
          session.authorizationVersion === args.authorizationVersion + 1))) fail("STALE_MEDIA_RESULT");
    const reserved = session.slot;
    if (reserved.kind === "empty" || reserved.connectionId !== args.connectionId ||
        reserved.leaseVersion !== args.leaseVersion || reserved.expiresAt <= now) fail("RESERVATION_UNAVAILABLE");
    await helperEligible(ctx, session, reserved.helperId, now);
    const room = await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", args.roomId)).unique();
    if (!room || room.state !== "active" || room.roomSid !== args.roomSid) fail("STALE_MEDIA_RESULT");
    const permit = await ctx.db.query("mediaTokens").withIndex("by_room_identity", q => q.eq("roomKey", room._id).eq("identity", args.connectionId)).unique();
    if (!permit || permit.state !== "issued" || permit.role !== "helper" || permit.userId !== reserved.helperId ||
        permit.leaseVersion !== args.leaseVersion || (permit.participantSid && permit.participantSid !== args.participantSid)) fail("UNISSUED_CONNECTION");
    if (reserved.kind === "connected") {
      if (reserved.participantSid !== args.participantSid) fail("CONNECTION_UNAVAILABLE");
      requireConnection(session, args, now);
      return session.authorizationVersion;
    }
    const expiresAt = Math.min(now + LEASE_TTL_MS, session.ownerExpiresAt, session.sharingExpiresAt);
    const slot = { ...reserved, kind: "connected" as const, participantSid: args.participantSid, expiresAt, authorizationExpiresAt: expiresAt };
    const authorizationVersion = nextVersion(session.authorizationVersion);
    await ctx.db.patch(permit._id, { participantSid: args.participantSid });
    await ctx.db.patch(session._id, { slot, authorizationVersion });
    await scheduleSlotExpiry(ctx, { ...session, slot });
    await scheduleSharingExpiry(ctx, { ...session, authorizationVersion });
    return authorizationVersion;
}

export const confirmConnection = internalMutation({
  args: { ...scopeArgs, roomId: v.string(), roomSid: v.string(), participantSid: v.string() },
  handler: confirmVerifiedConnection,
});

// Helper liveness never extends owner availability or sharing authorization.
export const heartbeat = mutation({
  args: scopeArgs,
  handler: async (ctx, args) => {
    const helper = await requireUser(ctx);
    const session = await sessionFor(ctx, args);
    const now = Date.now();
    await helperEligible(ctx, session, helper._id, now);
    const slot = requireConnection(session, args, now);
    if (slot.helperId !== helper._id || session.authorizationVersion !== args.authorizationVersion) fail("CONNECTION_UNAVAILABLE");
    const updated = { ...slot, expiresAt: Math.min(now + LEASE_TTL_MS, session.ownerExpiresAt) };
    await ctx.db.patch(session._id, { slot: updated });
    await scheduleSlotExpiry(ctx, { ...session, slot: updated });
    return { expiresAt: updated.expiresAt };
  },
});

export const renewAuthorization = mutation({
  args: { ...sessionArgs, authorizationVersion: v.number(),
    helper: v.union(v.null(), v.object({ connectionId: v.string(), leaseVersion: v.number() })), nonce: v.string() },
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    const now = Date.now();
    requireLive(session, now);
    renewalNonce(args.nonce);
    if (!session.sharing || session.media.kind !== "ready" || !session.media.ownerConnected || session.cleanupPending ||
        session.authorizationVersion !== args.authorizationVersion) fail("SHARING_UNAVAILABLE");
    if (session.sharingExpiresAt > 0 && session.sharingExpiresAt <= now) fail("SHARING_LEASE_EXPIRED");
    let deadline = Math.min(now + LEASE_TTL_MS, session.ownerExpiresAt);
    if (args.helper === null) {
      if (session.slot.kind === "connected") fail("SCOPE_CHANGED");
    } else {
      const slot = requireConnection(session, args.helper, now);
      await helperEligible(ctx, session, slot.helperId, now);
      deadline = Math.min(deadline, slot.expiresAt);
      const updated = { ...slot, authorizationExpiresAt: deadline };
      await ctx.db.patch(session._id, { slot: updated });
      await scheduleSlotExpiry(ctx, { ...session, slot: updated });
    }
    await ctx.db.patch(session._id, { sharingExpiresAt: deadline });
    await scheduleSharingExpiry(ctx, { ...session, sharingExpiresAt: deadline });
    return {
      scope: { sessionId: session._id, sessionEpoch: session.epoch,
        authorizationVersion: session.authorizationVersion, helper: args.helper },
      nonce: args.nonce, validForMs: deadline - now,
    };
  },
});

// Service ingress is deliberately absent. A future authenticated worker adapter
// may read this grant; it cannot extend authorization beyond the owner's grant.
export const workerAuthorization = internalMutation({
  args: { ...scopeArgs, nonce: v.string() },
  handler: async (ctx, args) => {
    const session = await sessionFor(ctx, args);
    const now = Date.now();
    renewalNonce(args.nonce);
    const slot = requireConnection(session, args, now);
    await helperEligible(ctx, session, slot.helperId, now);
    if (session.authorizationVersion !== args.authorizationVersion) fail("SCOPE_CHANGED");
    return {
      scope: { sessionId: session._id, sessionEpoch: session.epoch, authorizationVersion: session.authorizationVersion,
        helper: { connectionId: slot.connectionId, leaseVersion: slot.leaseVersion } },
      nonce: args.nonce,
      validForMs: Math.min(LEASE_TTL_MS, session.ownerExpiresAt - now, session.sharingExpiresAt - now, slot.expiresAt - now, slot.authorizationExpiresAt - now),
    };
  },
});

export const leave = mutation({
  args: connectionArgs,
  handler: async (ctx, args) => {
    const helper = await requireUser(ctx);
    const session = await sessionFor(ctx, args);
    const slot = session.slot;
    // Stale leave is harmless and cannot release a newer connection.
    if (slot.kind === "empty" || slot.helperId !== helper._id ||
        slot.connectionId !== args.connectionId || slot.leaseVersion !== args.leaseVersion) return;
    await invalidate(ctx, session, "helper_left");
  },
});

export const removeHelper = mutation({
  args: { ...sessionArgs, helperId: v.id("users") },
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    requireLive(session, Date.now());
    const removal = await ctx.db.query("sessionRemovals").withIndex("by_session_helper", q =>
      q.eq("sessionId", session._id).eq("helperId", args.helperId)).unique();
    if (removal) await ctx.db.patch(removal._id, { removed: true });
    else await ctx.db.insert("sessionRemovals", { sessionId: session._id, helperId: args.helperId, removed: true });
    if (session.slot.kind !== "empty" && session.slot.helperId === args.helperId) await invalidate(ctx, session, "owner_removed");
  },
});

export const restoreHelper = mutation({
  args: { ...sessionArgs, helperId: v.id("users") },
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    requireLive(session, Date.now());
    const removal = await ctx.db.query("sessionRemovals").withIndex("by_session_helper", q =>
      q.eq("sessionId", session._id).eq("helperId", args.helperId)).unique();
    if (removal) await ctx.db.patch(removal._id, { removed: false });
  },
});

export const expire = internalMutation({
  args: { ...connectionArgs, expectedExpiresAt: v.number() },
  handler: async (ctx, args) => {
    const session = await ctx.db.get(args.sessionId);
    if (!session || session.epoch !== args.epoch || session.slot.kind === "empty") return;
    const slot = session.slot;
    const deadline = slot.kind === "reserved" ? slot.expiresAt : Math.min(slot.expiresAt, slot.authorizationExpiresAt);
    if (slot.connectionId !== args.connectionId || slot.leaseVersion !== args.leaseVersion ||
        deadline !== args.expectedExpiresAt || deadline > Date.now()) return;
    await invalidate(ctx, session, "helper_expired");
  },
});
