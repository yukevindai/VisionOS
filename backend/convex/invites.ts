"use node";

import { createHash, randomBytes } from "node:crypto";
import { ConvexError, v } from "convex/values";
import { action } from "./_generated/server";
import type { ActionCtx } from "./_generated/server";
import { internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";

const MIN_LIFETIME_MS = 5 * 60 * 1000;
const MAX_LIFETIME_MS = 7 * 24 * 60 * 60 * 1000;

function hashCode(code: string): string {
  return createHash("sha256").update(code, "utf8").digest("hex");
}

async function chargeAttempt(ctx: Pick<ActionCtx, "runMutation">, operation: "create" | "redeem"): Promise<void> {
  const result = await ctx.runMutation(internal.inviteLimits.consumeAttempt, { operation });
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

export const createInvite = action({
  args: { lifetimeMs: v.any(), boundHelperId: v.optional(v.id("users")) },
  handler: async (ctx, args): Promise<{ inviteId: Id<"friendInvites">; code: string; expiresAt: number }> => {
    const lifetimeMs: unknown = args.lifetimeMs;
    const { boundHelperId } = args;
    await chargeAttempt(ctx, "create");
    if (typeof lifetimeMs !== "number" || !Number.isSafeInteger(lifetimeMs) || lifetimeMs < MIN_LIFETIME_MS || lifetimeMs > MAX_LIFETIME_MS) {
      throw new Error("Invitation lifetime must be between 5 minutes and 7 days");
    }
    const code = randomBytes(32).toString("base64url");
    const { inviteId, expiresAt } = await ctx.runMutation(internal.friends.storeInvite, {
      codeHash: hashCode(code), lifetimeMs, boundHelperId,
    });
    return { inviteId, code, expiresAt };
  },
});

export const redeemInvite = action({
  args: { code: v.any() },
  handler: async (ctx, args): Promise<{ state: "pending" | "accepted"; ownerId: Id<"users"> }> => {
    const code: unknown = args.code;
    await chargeAttempt(ctx, "redeem");
    if (typeof code !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(code)) throw new Error("Invitation unavailable");
    return ctx.runMutation(internal.friends.consumeInvite, { codeHash: hashCode(code) });
  },
});
