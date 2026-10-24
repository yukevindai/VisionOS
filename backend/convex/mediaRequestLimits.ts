import { v } from "convex/values";
import { internalMutation } from "./_generated/server";
import { requireUser } from "./lib/auth";

// Initial operational policy. Each operation has its own per-account budget;
// the observed provider capacity can guide future tuning.
const POLICY = {
  provision: { burst: 3, intervalMs: 30_000 },
  ownerToken: { burst: 6, intervalMs: 10_000 },
  helperToken: { burst: 6, intervalMs: 10_000 },
} satisfies Record<"provision" | "ownerToken" | "helperToken", { burst: number; intervalMs: number }>;

export type MediaRequestResult =
  | { kind: "allowed" }
  | { kind: "rateLimited"; retryAfterMs: number };

// One durable GCRA row per verified user and server-selected operation. This
// separate committed mutation charges even if subsequent provider work fails.
export const consumeAttempt = internalMutation({
  args: { operation: v.union(v.literal("provision"), v.literal("ownerToken"), v.literal("helperToken")) },
  handler: async (ctx, { operation }): Promise<MediaRequestResult> => {
    const user = await requireUser(ctx);
    const { burst, intervalMs } = POLICY[operation];
    const now = Date.now();
    const budget = await ctx.db.query("mediaRequestBudgets")
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
      await ctx.db.insert("mediaRequestBudgets", {
        userId: user._id, operation, theoreticalArrivalAt: nextArrivalAt,
      });
    }
    return { kind: "allowed" };
  },
});
