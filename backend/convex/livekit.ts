"use node";

import { createHash, randomUUID } from "node:crypto";
import { ConvexError, v } from "convex/values";
import { action, internalAction, type ActionCtx } from "./_generated/server";
import { internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";
import { createLiveKitProvider, readLiveKitConfig, signJoinToken, verifyLiveKitWebhook } from "./lib/livekitProvider";

const scope = { sessionId: v.id("sessions"), epoch: v.string() };
type SessionArgs = { sessionId: Id<"sessions">; epoch: string };
type RoomResult = { roomName: string; roomSid: string; generation: number };
type TokenResult = RoomResult & { token: string; serverUrl: string; identity: string; expiresAt: number };

async function chargeMediaRequest(ctx: Pick<ActionCtx, "runMutation">, operation: "provision" | "ownerToken" | "helperToken"): Promise<void> {
  const result = await ctx.runMutation(internal.mediaRequestLimits.consumeAttempt, { operation });
  switch (result.kind) {
    case "allowed":
      return;
    case "rateLimited":
      throw new ConvexError({ code: "RATE_LIMITED", retryAfterMs: result.retryAfterMs });
    default: {
      const exhaustive: never = result;
      return exhaustive;
    }
  }
}

export const provisionRoom = action({
  args: scope,
  handler: async (ctx, args): Promise<RoomResult> => {
    await chargeMediaRequest(ctx, "provision");
    const provider = createLiveKitProvider();
    const room = await ctx.runMutation(internal.mediaState.beginProvision, { ...args, roomName: `reflex-${randomUUID()}` });
    if (room.state === "active" && room.roomSid) return { roomName: room.roomId, roomSid: room.roomSid, generation: room.generation };
    try {
      const created = await provider.createRoom({ name: room.roomId });
      if (created.name !== room.roomId) throw new Error("INVALID_PROVIDER_RESULT");
      const accepted = await ctx.runMutation(internal.mediaState.finishProvision, { roomKey: room._id, roomSid: created.sid });
      if (!accepted) throw new Error("STALE_MEDIA_RESULT");
      return { roomName: room.roomId, roomSid: created.sid, generation: room.generation };
    } catch {
      await ctx.runMutation(internal.mediaState.failProvision, { roomKey: room._id });
      throw new Error("MEDIA_PROVISION_UNAVAILABLE");
    }
  },
});

async function issueToken(ctx: ActionCtx, args: SessionArgs & { clientInstanceId: string }, role: "owner" | "helper"): Promise<TokenResult> {
  await chargeMediaRequest(ctx, role === "owner" ? "ownerToken" : "helperToken");
  const config = readLiveKitConfig();
  const provider = createLiveKitProvider(config);
  const permit = await ctx.runMutation(internal.mediaState.prepareToken, { ...args, role, attemptId: randomUUID() });
  const room = await provider.getRoom(permit.roomName);
  if (!room || room.sid !== permit.roomSid) throw new Error("MEDIA_UNAVAILABLE");
  const token = await signJoinToken({ roomName: permit.roomName, identity: permit.identity, role, expiresAtMs: permit.expiresAt }, config);
  const accepted = await ctx.runMutation(internal.mediaState.finalizeToken, { permitId: permit.permitId, attemptId: permit.attemptId, roomSid: room.sid });
  if (!accepted) throw new Error("MEDIA_AUTHORIZATION_CHANGED");
  return { token, serverUrl: config.serverUrl, roomName: permit.roomName, roomSid: room.sid,
    generation: permit.generation, identity: permit.identity, expiresAt: permit.expiresAt };
}

export const issueOwnerToken = action({
  args: { ...scope, clientInstanceId: v.string() }, handler: (ctx, args): Promise<TokenResult> => issueToken(ctx, args, "owner"),
});
export const issueHelperToken = action({
  args: { ...scope, clientInstanceId: v.string() }, handler: (ctx, args): Promise<TokenResult> => issueToken(ctx, args, "helper"),
});

export const receiveWebhook = internalAction({
  args: { body: v.string(), authorization: v.string() },
  handler: async (ctx, args): Promise<number> => {
    let config;
    try { config = readLiveKitConfig(); } catch { return 503; }
    let event;
    try { event = await verifyLiveKitWebhook(args, config); } catch { return 401; }
    if (!event.room) return 204;
    try {
      await ctx.runMutation(internal.mediaState.receiveEvent, {
        eventId: event.id, digest: createHash("sha256").update(args.body).digest("hex"),
        roomId: event.room.name, roomSid: event.room.sid, event: event.event,
      });
      return 204;
    } catch { return 503; }
  },
});

export const reconcile = internalAction({
  args: { roomKey: v.id("mediaRooms") },
  handler: async (ctx, args): Promise<void> => {
    const claim = await ctx.runMutation(internal.mediaState.claimReconcile, args);
    if (!claim) return;
    try {
      const provider = createLiveKitProvider();
      const room = await provider.getRoom(claim.roomId);
      const participants = room ? await provider.listParticipants(claim.roomId) : [];
      await ctx.runMutation(internal.mediaState.applySnapshot, { roomKey: claim._id, attempt: claim.reconcileAttempt, roomSid: room?.sid ?? null, participants });
    } catch { await ctx.runMutation(internal.mediaState.retryReconcile, { roomKey: claim._id, attempt: claim.reconcileAttempt }); }
  },
});

export const cleanup = internalAction({
  args: { cleanupId: v.id("mediaCleanup") },
  handler: async (ctx, args): Promise<void> => {
    const claim = await ctx.runMutation(internal.media.claimCleanup, args);
    if (!claim) return;
    try {
      const provider = createLiveKitProvider();
      // Explicit Cloud cutoff is defense in depth, not the isolation proof.
      // Even failed removals proceed to whole-room deletion and verification.
      await Promise.allSettled(claim.identities.map(identity => provider.removeParticipant({
        roomName: claim.roomId, identity, revokeBeforeSeconds: Math.floor(Date.now() / 1_000) + 1,
      })));
      await provider.deleteRoom(claim.roomId);
      if (await provider.getRoom(claim.roomId)) throw new Error("ROOM_STILL_PRESENT");
      await ctx.runMutation(internal.media.finishCleanup, { cleanupId: claim._id, attempt: claim.attempt, roomId: claim.roomId });
    } catch { await ctx.runMutation(internal.media.retryCleanup, { cleanupId: claim._id, attempt: claim.attempt }); }
  },
});
