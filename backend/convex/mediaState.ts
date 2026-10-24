import { v } from "convex/values";
import { internalMutation, internalQuery, mutation } from "./_generated/server";
import { internal } from "./_generated/api";
import { requireUser } from "./lib/auth";
import { fail, helperEligible, invalidate, nextVersion, opaque, ownerSession, requireLive, sessionArgs, sessionFor } from "./lib/leases";
import { queueRoomCleanup } from "./lib/mediaLifecycle";
import { confirmVerifiedConnection } from "./assistance";

export const beginProvision = internalMutation({
  args: { ...sessionArgs, roomName: v.string() },
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    requireLive(session, Date.now());
    if (session.cleanupPending) fail("MEDIA_CLEANUP_REQUIRED");
    if (session.media.kind === "ready" || session.media.kind === "provisioning") {
      const name = session.media.roomId;
      const existing = await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", name)).unique();
      if (!existing) fail("MEDIA_LEDGER_MISSING");
      return existing;
    }
    if (!/^reflex-[0-9a-f-]{36}$/.test(args.roomName)) fail("INVALID_ROOM_NAME");
    if (await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", args.roomName)).first()) fail("ROOM_ALREADY_BOUND");
    const generation = nextVersion(session.nextMediaGeneration);
    const roomKey = await ctx.db.insert("mediaRooms", {
      sessionId: session._id, epoch: session.epoch, generation, roomId: args.roomName,
      state: "provisioning", authorizationVersion: session.authorizationVersion, createdAt: Date.now(),
      auditAfter: Date.now() + 60_000, provisionExpiresAt: session.ownerExpiresAt, reconcileAttempt: 0, reconcileAfter: 0,
    });
    await ctx.db.patch(session._id, { nextMediaGeneration: generation, media: { kind: "provisioning", roomId: args.roomName, generation } });
    await ctx.scheduler.runAt(session.ownerExpiresAt, internal.mediaState.provisionExpired, { roomKey });
    const room = await ctx.db.get(roomKey);
    if (!room) fail("MEDIA_LEDGER_MISSING");
    return room;
  },
});

export const finishProvision = internalMutation({
  args: { roomKey: v.id("mediaRooms"), roomSid: v.string() },
  handler: async (ctx, args) => {
    const room = await ctx.db.get(args.roomKey);
    if (!room || !args.roomSid || args.roomSid.length > 256) fail("INVALID_PROVIDER_RESULT");
    const session = await ctx.db.get(room.sessionId);
    const current = session && session.status === "active" && session.ownerExpiresAt > Date.now() &&
      session.media.kind !== "unready" && session.media.roomId === room.roomId && !session.cleanupPending;
    if (room.state === "active" && current && room.roomSid === args.roomSid) return true;
    if (!current || session.authorizationVersion !== room.authorizationVersion || room.state !== "provisioning" || room.provisionExpiresAt <= Date.now()) {
      await queueRoomCleanup(ctx, room, room.authorizationVersion, "stale_provision");
      if (session && session.media.kind !== "unready" && session.media.roomId === room.roomId) {
        await invalidate(ctx, session, "media_recovery", true);
        await ctx.db.patch(session._id, { cleanupPending: true });
      }
      return false;
    }
    await ctx.db.patch(room._id, { state: "active", roomSid: args.roomSid });
    await ctx.db.patch(session._id, { media: { kind: "ready", roomId: room.roomId, generation: room.generation, roomSid: args.roomSid, ownerConnected: false } });
    await ctx.scheduler.runAfter(0, internal.livekit.reconcile, { roomKey: room._id });
    return true;
  },
});

export const provisionExpired = internalMutation({
  args: { roomKey: v.id("mediaRooms") },
  handler: async (ctx, { roomKey }) => {
    const room = await ctx.db.get(roomKey);
    if (!room || room.state !== "provisioning" || room.provisionExpiresAt > Date.now()) return;
    const session = await ctx.db.get(room.sessionId);
    if (session && session.media.kind !== "unready" && session.media.roomId === room.roomId) {
      await invalidate(ctx, session, "media_recovery", true);
    } else await queueRoomCleanup(ctx, room, room.authorizationVersion, "provision_expired");
  },
});

export const failProvision = internalMutation({
  args: { roomKey: v.id("mediaRooms") },
  handler: async (ctx, { roomKey }) => {
    const room = await ctx.db.get(roomKey);
    if (!room || room.state === "active") return;
    const session = await ctx.db.get(room.sessionId);
    await queueRoomCleanup(ctx, room, room.authorizationVersion, "provision_failed");
    if (session && session.media.kind !== "unready" && session.media.roomId === room.roomId) {
      await invalidate(ctx, session, "media_recovery", true);
      await ctx.db.patch(session._id, { cleanupPending: true });
    }
  },
});

export const recover = mutation({
  args: sessionArgs,
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    requireLive(session, Date.now());
    await invalidate(ctx, session, "media_recovery", true);
  },
});

export const prepareToken = internalMutation({
  args: { ...sessionArgs, role: v.union(v.literal("owner"), v.literal("helper")), clientInstanceId: v.string(), attemptId: v.string() },
  handler: async (ctx, args) => {
    const user = await requireUser(ctx);
    const session = await sessionFor(ctx, args);
    requireLive(session, Date.now());
    opaque(args.clientInstanceId); opaque(args.attemptId);
    if (session.media.kind !== "ready" || session.cleanupPending) fail("MEDIA_UNAVAILABLE");
    const roomName = session.media.roomId;
    const room = await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", roomName)).unique();
    if (!room || room.state !== "active") fail("MEDIA_UNAVAILABLE");
    let identity: string;
    let leaseVersion: number | undefined;
    let expiresAt = Math.min(Date.now() + 120_000, session.ownerExpiresAt);
    if (args.role === "owner") {
      if (session.ownerId !== user._id) fail("SESSION_UNAVAILABLE");
      identity = `owner:${room._id}`;
    } else {
      await helperEligible(ctx, session, user._id, Date.now());
      const slot = session.slot;
      if (slot.kind !== "reserved" || slot.helperId !== user._id || slot.clientInstanceId !== args.clientInstanceId || slot.expiresAt <= Date.now()) fail("RESERVATION_UNAVAILABLE");
      identity = slot.connectionId;
      leaseVersion = slot.leaseVersion;
      expiresAt = Math.min(expiresAt, slot.expiresAt, session.sharingExpiresAt);
    }
    const existing = await ctx.db.query("mediaTokens").withIndex("by_room_identity", q => q.eq("roomKey", room._id).eq("identity", identity)).unique();
    if (existing?.state === "issued") fail("TOKEN_ALREADY_ISSUED");
    const data = { roomKey: room._id, userId: user._id, identity, role: args.role, clientInstanceId: args.clientInstanceId,
      authorizationVersion: session.authorizationVersion, leaseVersion, attemptId: args.attemptId, expiresAt, state: "prepared" as const };
    const permitId = existing ? existing._id : await ctx.db.insert("mediaTokens", data);
    if (existing) await ctx.db.replace(existing._id, data);
    return { ...data, permitId, roomName: room.roomId, roomSid: room.roomSid, generation: room.generation };
  },
});

export const finalizeToken = internalMutation({
  args: { permitId: v.id("mediaTokens"), attemptId: v.string(), roomSid: v.string() },
  handler: async (ctx, args) => {
    const user = await requireUser(ctx);
    const permit = await ctx.db.get(args.permitId);
    if (!permit || permit.userId !== user._id || permit.state !== "prepared" || permit.attemptId !== args.attemptId || permit.expiresAt <= Date.now()) return false;
    const room = await ctx.db.get(permit.roomKey);
    const session = room && await ctx.db.get(room.sessionId);
    if (!room || room.state !== "active" || room.roomSid !== args.roomSid || !session || session.epoch !== room.epoch ||
        session.status !== "active" || session.ownerExpiresAt <= Date.now() || session.cleanupPending ||
        session.media.kind !== "ready" || session.media.roomId !== room.roomId || session.authorizationVersion !== permit.authorizationVersion) return false;
    if (permit.role === "owner") {
      if (session.ownerId !== user._id) return false;
    } else {
      try { await helperEligible(ctx, session, user._id, Date.now()); } catch { return false; }
      const slot = session.slot;
      if (slot.kind !== "reserved" || slot.helperId !== user._id || slot.connectionId !== permit.identity || slot.leaseVersion !== permit.leaseVersion || slot.expiresAt <= Date.now()) return false;
    }
    await ctx.db.patch(permit._id, { state: "issued" });
    return true;
  },
});

export const claimReconcile = internalMutation({
  args: { roomKey: v.id("mediaRooms") },
  handler: async (ctx, args) => {
    const room = await ctx.db.get(args.roomKey);
    if (!room || room.state !== "active" || room.reconcileAfter > Date.now()) return null;
    const session = await ctx.db.get(room.sessionId);
    if (!session || session.status !== "active" || session.ownerExpiresAt <= Date.now()) {
      if (session) await invalidate(ctx, session, "media_recovery", true);
      return null;
    }
    const attempt = nextVersion(room.reconcileAttempt);
    await ctx.db.patch(room._id, { reconcileAttempt: attempt, reconcileAfter: Date.now() + 30_000 });
    await ctx.scheduler.runAfter(30_000, internal.livekit.reconcile, { roomKey: room._id });
    return { ...room, reconcileAttempt: attempt };
  },
});

export const applySnapshot = internalMutation({
  args: { roomKey: v.id("mediaRooms"), attempt: v.number(), roomSid: v.union(v.string(), v.null()),
    participants: v.array(v.object({ identity: v.string(), sid: v.string(), state: v.union(v.literal("active"), v.literal("other")) })) },
  handler: async (ctx, args) => {
    const room = await ctx.db.get(args.roomKey);
    if (!room || room.state !== "active" || room.reconcileAttempt !== args.attempt || room.reconcileAfter <= Date.now()) return;
    const session = await ctx.db.get(room.sessionId);
    if (!session || session.media.kind !== "ready" || session.media.roomId !== room.roomId) return;
    if (session.status !== "active" || session.ownerExpiresAt <= Date.now() || args.roomSid !== room.roomSid || args.participants.length > 3) {
      await invalidate(ctx, await ctx.db.get(session._id) ?? session, "media_mismatch", true); return;
    }
    const permits = await ctx.db.query("mediaTokens").withIndex("by_room", q => q.eq("roomKey", room._id)).collect();
    let ownerPresent = false;
    for (const participant of args.participants) {
      const permit = permits.find(p => p.identity === participant.identity && p.state === "issued");
      if (!permit || (permit.participantSid && permit.participantSid !== participant.sid) ||
          (!permit.participantSid && permit.expiresAt <= Date.now())) {
        await invalidate(ctx, await ctx.db.get(session._id) ?? session, "media_mismatch", true); return;
      }
      if (participant.state !== "active") continue;
      if (permit.role === "owner") {
        ownerPresent = true;
        await ctx.db.patch(permit._id, { participantSid: participant.sid });
      } else {
        try {
          await confirmVerifiedConnection(ctx, {
            sessionId: session._id, epoch: session.epoch, roomId: room.roomId,
            connectionId: permit.identity, leaseVersion: permit.leaseVersion ?? -1,
            authorizationVersion: permit.authorizationVersion, roomSid: args.roomSid,
            participantSid: participant.sid,
          });
        } catch { await invalidate(ctx, await ctx.db.get(session._id) ?? session, "media_mismatch", true); return; }
      }
    }
    if (!ownerPresent && permits.some(p => p.role === "owner" && p.participantSid)) {
      await invalidate(ctx, await ctx.db.get(session._id) ?? session, "media_mismatch", true); return;
    }
    const slot = session.slot;
    if (slot.kind === "connected" && !args.participants.some(p => p.identity === slot.connectionId && p.sid === slot.participantSid && p.state === "active")) {
      await invalidate(ctx, await ctx.db.get(session._id) ?? session, "media_mismatch", true); return;
    }
    await ctx.db.patch(session._id, { media: { ...session.media, ownerConnected: ownerPresent } });
    await ctx.db.patch(room._id, { reconcileAfter: Date.now() + 5_000 });
    await ctx.scheduler.runAfter(5_000, internal.livekit.reconcile, { roomKey: room._id });
  },
});

export const retryReconcile = internalMutation({
  args: { roomKey: v.id("mediaRooms"), attempt: v.number() },
  handler: async (ctx, args) => {
    const room = await ctx.db.get(args.roomKey);
    if (!room || room.state !== "active" || room.reconcileAttempt !== args.attempt) return;
    await ctx.db.patch(room._id, { reconcileAfter: Date.now() + 5_000 });
    await ctx.scheduler.runAfter(5_000, internal.livekit.reconcile, { roomKey: room._id });
  },
});

export const receiveEvent = internalMutation({
  args: { eventId: v.string(), digest: v.string(), roomId: v.string(), roomSid: v.string(), event: v.string() },
  handler: async (ctx, args) => {
    const prior = await ctx.db.query("mediaEvents").withIndex("by_event", q => q.eq("eventId", args.eventId)).unique();
    if (prior) { if (prior.digest !== args.digest) fail("EVENT_COLLISION"); return; }
    const room = await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", args.roomId)).unique();
    if (!room) return;
    await ctx.db.insert("mediaEvents", { ...args, receivedAt: Date.now() });
    if (room.state === "retired" || room.state === "closed") {
      await queueRoomCleanup(ctx, room, room.authorizationVersion, "retired_room_event"); return;
    }
    // Event contents never directly grant/release a lease. Reconcile the actual
    // room SID and participant SID snapshot, including on stale left events.
    if (room.state === "active") {
      await ctx.scheduler.runAfter(Math.max(0, room.reconcileAfter - Date.now()), internal.livekit.reconcile, { roomKey: room._id });
    }
  },
});

export const getRoom = internalQuery({ args: { roomKey: v.id("mediaRooms") }, handler: (ctx, args) => ctx.db.get(args.roomKey) });
