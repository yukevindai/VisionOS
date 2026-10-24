import { v } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { friendPageOptions, friendPageResult } from "./lib/friendPagination";
import { internalMutation, mutation, query } from "./_generated/server";
import { requireUser } from "./lib/auth";
import { fail, invalidate, opaque, ownerSession, OWNER_TTL_MS, requireLive, scheduleOwnerExpiry, sessionArgs } from "./lib/leases";

export const start = mutation({
  args: { epoch: v.string() },
  handler: async (ctx, args) => {
    const owner = await requireUser(ctx);
    opaque(args.epoch);
    const prior = await ctx.db.query("sessions").withIndex("by_owner_epoch", q =>
      q.eq("ownerId", owner._id).eq("epoch", args.epoch)).unique();
    // A retry returns the original identity; it never revives an expired/ended session.
    if (prior) return prior;
    const now = Date.now();
    const existing = await ctx.db.query("sessions").withIndex("by_owner_status", q => q.eq("ownerId", owner._id).eq("status", "active")).collect();
    for (const session of existing) {
      if (session.status === "active") {
        await invalidate(ctx, session, "superseded", true);
        await ctx.db.patch(session._id, { status: "ended", sharing: false, endedAt: now, endReason: "superseded" });
      }
    }
    const sessionId = await ctx.db.insert("sessions", {
      ownerId: owner._id, epoch: args.epoch, status: "active", sharing: owner.automaticSharing,
      authorizationVersion: 1, nextLeaseVersion: 0, nextMediaGeneration: 0, ownerExpiresAt: now + OWNER_TTL_MS, sharingExpiresAt: 0,
      createdAt: now, media: { kind: "unready" }, cleanupPending: false, slot: { kind: "empty" },
    });
    const session = await ctx.db.get(sessionId);
    if (!session) fail("SESSION_UNAVAILABLE");
    await scheduleOwnerExpiry(ctx, session);
    return session;
  },
});

export const get = query({ args: sessionArgs, handler: ownerSession });

// Native polling lease uses elapsed time from REQUEST SEND, never server/client
// wall-clock subtraction. The full sharing grant still requires renewAuthorization.
export const mediaStatus = query({
  args: { ...sessionArgs, nonce: v.string() },
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    if (!/^[A-Za-z0-9_:-]{1,256}$/.test(args.nonce)) fail("INVALID_NONCE");
    const validForMs = session.status === "active"
      ? Math.max(0, Math.min(15_000, session.ownerExpiresAt - Date.now())) : 0;
    const slot = session.slot.kind === "empty" ? { kind: "empty" as const }
      : session.slot.kind === "reserved"
        ? { kind: "reserved" as const, connectionId: session.slot.connectionId, leaseVersion: session.slot.leaseVersion }
        : { kind: "connected" as const, connectionId: session.slot.connectionId, leaseVersion: session.slot.leaseVersion,
            participantSid: session.slot.participantSid };
    return { sessionId: session._id, epoch: session.epoch,
      status: validForMs > 0 ? "active" as const : "ended" as const,
      authorizationVersion: session.authorizationVersion, sharing: session.sharing,
      cleanupPending: session.cleanupPending, media: session.media, slot, validForMs, nonce: args.nonce };
  },
});

export const heartbeat = mutation({
  args: sessionArgs,
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    const now = Date.now();
    requireLive(session, now);
    const ownerExpiresAt = now + OWNER_TTL_MS;
    await ctx.db.patch(session._id, { ownerExpiresAt });
    await scheduleOwnerExpiry(ctx, { ...session, ownerExpiresAt });
    return { ownerExpiresAt };
  },
});

export const setSharing = mutation({
  args: { ...sessionArgs, enabled: v.boolean() },
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    requireLive(session, Date.now());
    if (session.sharing === args.enabled && !(args.enabled && session.sharingExpiresAt > 0 && session.sharingExpiresAt <= Date.now())) return;
    if (args.enabled && session.cleanupPending) fail("MEDIA_CLEANUP_REQUIRED");
    await invalidate(ctx, session, "sharing_changed");
    await ctx.db.patch(session._id, { sharing: args.enabled });
  },
});

export const end = mutation({
  args: sessionArgs,
  handler: async (ctx, args) => {
    const session = await ownerSession(ctx, args);
    if (session.status === "ended") return;
    await invalidate(ctx, session, "owner_ended", true);
    await ctx.db.patch(session._id, { status: "ended", sharing: false, endedAt: Date.now(), endReason: "owner_ended" });
  },
});

export const listJoinable = query({
  args: { paginationOpts: paginationOptsValidator },
  handler: async (ctx, { paginationOpts }) => {
    const helper = await requireUser(ctx);
    const scope = { userId: helper._id, view: "joinableSessions" as const };
    const grants = await ctx.db.query("friendships")
      .withIndex("by_helper_state", q => q.eq("helperId", helper._id).eq("state", "accepted"))
      .order("desc").paginate(friendPageOptions(paginationOpts, scope));
    const result = [];
    const now = Date.now();
    for (const grant of grants.page) {
      // Start atomically supersedes prior sessions. If legacy/corrupt data violates
      // the single-active-session invariant, omit this owner rather than guess.
      const sessions = await ctx.db.query("sessions")
        .withIndex("by_owner_status", q => q.eq("ownerId", grant.ownerId).eq("status", "active"))
        .take(2);
      if (sessions.length !== 1) continue;
      const session = sessions[0];
      if (!session || session.ownerId === helper._id || session.status !== "active" ||
          session.ownerExpiresAt <= now || !session.sharing || session.sharingExpiresAt <= now ||
          session.media.kind !== "ready" || !session.media.ownerConnected ||
          session.cleanupPending || session.slot.kind !== "empty") continue;
      const removal = await ctx.db.query("sessionRemovals").withIndex("by_session_helper", q =>
        q.eq("sessionId", session._id).eq("helperId", helper._id)).unique();
      if (removal?.removed) continue;
      const owner = await ctx.db.get(session.ownerId);
      if (!owner) continue;
      result.push({ sessionId: session._id, epoch: session.epoch, ownerDisplayName: owner.displayName });
    }
    // Filtering can empty a page. The cursor still advances through accepted
    // friendships; discovery never reserves a helper slot or grants media access.
    return friendPageResult({ ...grants, page: result }, scope);
  },
});

export const expire = internalMutation({
  args: { ...sessionArgs, expectedExpiresAt: v.number() },
  handler: async (ctx, args) => {
    const session = await ctx.db.get(args.sessionId);
    if (!session || session.epoch !== args.epoch || session.status !== "active" ||
        session.ownerExpiresAt !== args.expectedExpiresAt || session.ownerExpiresAt > Date.now()) return;
    await invalidate(ctx, session, "owner_expired", true);
    await ctx.db.patch(session._id, { status: "ended", sharing: false, endedAt: Date.now(), endReason: "owner_expired" });
  },
});

export const expireSharing = internalMutation({
  args: { ...sessionArgs, authorizationVersion: v.number(), expectedExpiresAt: v.number() },
  handler: async (ctx, args) => {
    const session = await ctx.db.get(args.sessionId);
    if (!session || session.epoch !== args.epoch || session.status !== "active" || !session.sharing ||
        session.authorizationVersion !== args.authorizationVersion || session.sharingExpiresAt !== args.expectedExpiresAt ||
        session.sharingExpiresAt > Date.now()) return;
    await invalidate(ctx, session, "sharing_expired");
    await ctx.db.patch(session._id, { sharing: false });
  },
});
