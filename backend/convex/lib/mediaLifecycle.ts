import type { Doc } from "../_generated/dataModel";
import type { MutationCtx } from "../_generated/server";
import { internal } from "../_generated/api";

export async function queueRoomCleanup(ctx: MutationCtx, room: Doc<"mediaRooms">, authorizationVersion: number, reason: string) {
  await ctx.db.patch(room._id, { state: "retired" });
  const pending = await ctx.db.query("mediaCleanup").withIndex("by_room_state", q =>
    q.eq("roomKey", room._id).eq("state", "pending")).first();
  if (pending) return pending._id;
  const cleanupId = await ctx.db.insert("mediaCleanup", {
    sessionId: room.sessionId, epoch: room.epoch, authorizationVersion, roomKey: room._id,
    roomId: room.roomId, target: { kind: "room" }, reason, state: "pending", createdAt: Date.now(),
    attempt: 0, nextAttemptAt: Date.now(), claimedUntil: 0,
  });
  await ctx.scheduler.runAfter(0, internal.livekit.cleanup, { cleanupId });
  return cleanupId;
}

export async function retireCurrentRoom(ctx: MutationCtx, session: Doc<"sessions">, authorizationVersion: number, reason: string) {
  if (session.media.kind === "unready" || session.media.kind === "retired") return;
  const roomId = session.media.roomId;
  const room = await ctx.db.query("mediaRooms").withIndex("by_name", q => q.eq("roomId", roomId)).unique();
  if (!room) throw new Error("MEDIA_LEDGER_MISSING");
  await queueRoomCleanup(ctx, room, authorizationVersion, reason);
  await ctx.db.patch(session._id, {
    media: { kind: "retired", roomId, generation: room.generation }, cleanupPending: true,
  });
}
