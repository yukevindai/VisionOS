import { v } from "convex/values";
import { internalMutation } from "./_generated/server";
import { internal } from "./_generated/api";
import { nextVersion } from "./lib/leases";
import { queueRoomCleanup } from "./lib/mediaLifecycle";

export const claimCleanup = internalMutation({
  args: { cleanupId: v.id("mediaCleanup") },
  handler: async (ctx, args) => {
    const job = await ctx.db.get(args.cleanupId);
    if (!job || job.state !== "pending" || job.nextAttemptAt > Date.now() || job.claimedUntil > Date.now()) return null;
    const room = await ctx.db.get(job.roomKey);
    if (!room || (room.state !== "retired" && room.state !== "closed")) return null;
    const attempt = nextVersion(job.attempt);
    await ctx.db.patch(job._id, { attempt, claimedUntil: Date.now() + 30_000 });
    // A watchdog recovers action crashes; scheduled actions aren't auto-retried.
    await ctx.scheduler.runAfter(30_000, internal.livekit.cleanup, { cleanupId: job._id });
    const permits = await ctx.db.query("mediaTokens").withIndex("by_room", q => q.eq("roomKey", room._id)).collect();
    return { ...job, attempt, identities: permits.map(p => p.identity) };
  },
});

export const finishCleanup = internalMutation({
  args: { cleanupId: v.id("mediaCleanup"), attempt: v.number(), roomId: v.string() },
  handler: async (ctx, args) => {
    const job = await ctx.db.get(args.cleanupId);
    if (!job || job.state !== "pending" || job.attempt !== args.attempt || job.roomId !== args.roomId || job.claimedUntil <= Date.now()) return;
    const room = await ctx.db.get(job.roomKey);
    if (!room || (room.state !== "retired" && room.state !== "closed")) return;
    // Provider deletion + absence is a snapshot. Permanent name retirement,
    // enforced publisher generation changes and repeat audits provide isolation.
    await ctx.db.patch(job._id, { state: "verified", verifiedAt: Date.now(), claimedUntil: 0 });
    await ctx.db.patch(room._id, { state: "closed", auditAfter: Date.now() + 60_000 });
    const session = await ctx.db.get(job.sessionId);
    if (session?.media.kind === "retired" && session.media.roomId === job.roomId) {
      const pending = await ctx.db.query("mediaCleanup").withIndex("by_room_state", q => q.eq("roomKey", room._id).eq("state", "pending")).first();
      if (!pending) await ctx.db.patch(session._id, { cleanupPending: false });
    }
  },
});

export const retryCleanup = internalMutation({
  args: { cleanupId: v.id("mediaCleanup"), attempt: v.number() },
  handler: async (ctx, args) => {
    const job = await ctx.db.get(args.cleanupId);
    if (!job || job.state !== "pending" || job.attempt !== args.attempt) return;
    const delay = Math.min(300_000, 1_000 * 2 ** Math.min(job.attempt, 9));
    await ctx.db.patch(job._id, { claimedUntil: 0, nextAttemptAt: Date.now() + delay, lastFailure: "provider_unavailable_or_room_present" });
    await ctx.scheduler.runAfter(delay, internal.livekit.cleanup, { cleanupId: job._id });
  },
});

// Bounded fair scan catches recreated tombstoned rooms even if webhooks are lost.
export const auditRetired = internalMutation({
  args: {},
  handler: async ctx => {
    const rooms = await ctx.db.query("mediaRooms").withIndex("by_state_audit", q => q.eq("state", "closed").lte("auditAfter", Date.now())).take(10);
    for (const room of rooms) await queueRoomCleanup(ctx, room, room.authorizationVersion, "retired_room_audit");
  },
});
