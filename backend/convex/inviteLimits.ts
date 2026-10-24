import { v } from "convex/values";
import { internalMutation } from "./_generated/server";
import { requireUser } from "./lib/auth";

const POLICY = {
  create: { burst: 5, intervalMs: 60_000 },
  redeem: { burst: 10, intervalMs: 10_000 },
} satisfies Record<"create" | "redeem", { burst: number; intervalMs: number }>;

export type InviteAttemptResult =
  | { kind: "allowed" }
  | { kind: "rateLimited"; retryAfterMs: number };

// GCRA keeps exactly one durable row per verified account and operation. The
// admitted attempt commits here, independently of later invitation failures.
export const consumeAttempt = internalMutation({
  args: { operation: v.union(v.literal("create"), v.literal("redeem")) },
  handler: async (ctx, { operation }): Promise<InviteAttemptResult> => {
    const user = await requireUser(ctx);
    const { burst, intervalMs } = POLICY[operation];
    const now = Date.now();
    const budget = await ctx.db.query("inviteAttemptBudgets")
      .withIndex("by_user_operation", q => q.eq("userId", user._id).eq("operation", operation))
      .unique();
    const theoreticalArrivalAt = budget?.theoreticalArrivalAt ?? now;
    const earliestAllowedAt = theoreticalArrivalAt - (burst - 1) * intervalMs;
    if (now < earliestAllowedAt) {
      return { kind: "rateLimited", retryAfterMs: earliestAllowedAt - now };
    }
    const nextArrivalAt = Math.max(now, theoreticalArrivalAt) + intervalMs;
    if (budget) {
      await ctx.db.patch(budget._id, { theoreticalArrivalAt: nextArrivalAt });
    } else {
      await ctx.db.insert("inviteAttemptBudgets", {
        userId: user._id, operation, theoreticalArrivalAt: nextArrivalAt,
      });
    }
    return { kind: "allowed" };
  },
});
