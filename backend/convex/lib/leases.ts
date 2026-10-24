import { ConvexError, v } from "convex/values";
import type { Doc, Id } from "../_generated/dataModel";
import type { MutationCtx, QueryCtx } from "../_generated/server";
import { internal } from "../_generated/api";
import { requireUser } from "./auth";
import { retireCurrentRoom } from "./mediaLifecycle";

export const OWNER_TTL_MS = 20_000;
export const LEASE_TTL_MS = 15_000;
export const sessionArgs = { sessionId: v.id("sessions"), epoch: v.string() };
export const connectionArgs = {
  ...sessionArgs, connectionId: v.string(), leaseVersion: v.number(),
};
export const scopeArgs = { ...connectionArgs, authorizationVersion: v.number() };
type ReadCtx = QueryCtx | MutationCtx;

export function fail(code: string): never { throw new ConvexError(code); }

export function nextVersion(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0 || value >= Number.MAX_SAFE_INTEGER) fail("VERSION_EXHAUSTED");
  return value + 1;
}

export function opaque(value: string): string {
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(value)) fail("INVALID_CORRELATION_ID");
  return value;
}

export function renewalNonce(value: string): string {
  if (value.length === 0 || value.length > 256 || !/^[A-Za-z0-9_:-]+$/.test(value)) fail("INVALID_NONCE");
  return value;
}

export async function scheduleSharingExpiry(ctx: MutationCtx, session: Doc<"sessions">) {
  await ctx.scheduler.runAt(session.sharingExpiresAt, internal.sessions.expireSharing, {
    sessionId: session._id, epoch: session.epoch, authorizationVersion: session.authorizationVersion,
    expectedExpiresAt: session.sharingExpiresAt,
  });
}

export async function sessionFor(
  ctx: ReadCtx, args: { sessionId: Id<"sessions">; epoch: string },
): Promise<Doc<"sessions">> {
  const session = await ctx.db.get(args.sessionId);
  if (!session || session.epoch !== args.epoch) fail("SESSION_UNAVAILABLE");
  return session;
}

export async function ownerSession(ctx: ReadCtx, args: { sessionId: Id<"sessions">; epoch: string }) {
  const owner = await requireUser(ctx);
  const session = await sessionFor(ctx, args);
  if (session.ownerId !== owner._id) fail("SESSION_UNAVAILABLE");
  return session;
}

export function requireLive(session: Doc<"sessions">, now: number) {
  if (session.status !== "active" || session.ownerExpiresAt <= now) fail("SESSION_UNAVAILABLE");
}

export async function helperEligible(ctx: ReadCtx, session: Doc<"sessions">, helperId: Id<"users">, now: number) {
  requireLive(session, now);
  if (!session.sharing || session.sharingExpiresAt <= now || session.cleanupPending || session.media.kind !== "ready" || !session.media.ownerConnected) fail("SHARING_UNAVAILABLE");
  const grant = await ctx.db.query("friendships").withIndex("by_owner_helper", q =>
    q.eq("ownerId", session.ownerId).eq("helperId", helperId)).unique();
  const removal = await ctx.db.query("sessionRemovals").withIndex("by_session_helper", q =>
    q.eq("sessionId", session._id).eq("helperId", helperId)).unique();
  if (grant?.state !== "accepted" || removal?.removed || helperId === session.ownerId) fail("SHARING_UNAVAILABLE");
}

export function requireConnection(session: Doc<"sessions">, args: { connectionId: string; leaseVersion: number }, now: number) {
  const slot = session.slot;
  if (slot.kind !== "connected" || slot.connectionId !== args.connectionId ||
      slot.leaseVersion !== args.leaseVersion || slot.expiresAt <= now || slot.authorizationExpiresAt <= now) {
    fail("CONNECTION_UNAVAILABLE");
  }
  return slot;
}

export async function scheduleOwnerExpiry(ctx: MutationCtx, session: Doc<"sessions">) {
  await ctx.scheduler.runAt(session.ownerExpiresAt, internal.sessions.expire, {
    sessionId: session._id, epoch: session.epoch, expectedExpiresAt: session.ownerExpiresAt,
  });
}

export async function scheduleSlotExpiry(ctx: MutationCtx, session: Doc<"sessions">) {
  if (session.slot.kind === "empty") return;
  const slot = session.slot;
  const deadline = slot.kind === "reserved" ? slot.expiresAt : Math.min(slot.expiresAt, slot.authorizationExpiresAt);
  await ctx.scheduler.runAt(deadline, internal.assistance.expire, {
    sessionId: session._id, epoch: session.epoch, connectionId: slot.connectionId,
    leaseVersion: slot.leaseVersion, expectedExpiresAt: deadline,
  });
}

// State changes commit atomically with durable cleanup intent. The Node
// provider path performs and verifies cleanup; no network I/O occurs here.
export async function invalidate(
  ctx: MutationCtx, session: Doc<"sessions">, reason: string, closeRoom = false,
) {
  const authorizationVersion = nextVersion(session.authorizationVersion);
  // Retire the entire generation even when only the helper loses access. A
  // deleted LiveKit room can be recreated by a cached token; never reuse it.
  if (closeRoom || session.slot.kind !== "empty" || reason === "sharing_expired" || reason === "media_recovery" || reason === "media_mismatch") {
    await retireCurrentRoom(ctx, session, authorizationVersion, reason);
  }
  await ctx.db.patch(session._id, { slot: { kind: "empty" }, authorizationVersion, sharingExpiresAt: 0 });
}

export async function invalidateFriendship(ctx: MutationCtx, ownerId: Id<"users">, helperId: Id<"users">) {
  const sessions = await ctx.db.query("sessions").withIndex("by_owner_status", q => q.eq("ownerId", ownerId).eq("status", "active")).collect();
  for (const session of sessions) {
    if (session.status === "active" && session.slot.kind !== "empty" && session.slot.helperId === helperId) {
      await invalidate(ctx, session, "friendship_revoked");
    }
  }
}
