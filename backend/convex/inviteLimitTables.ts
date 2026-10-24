import { defineTable } from "convex/server";
import { v } from "convex/values";

export const inviteLimitTables = {
  inviteAttemptBudgets: defineTable({
    userId: v.id("users"),
    operation: v.union(v.literal("create"), v.literal("redeem")),
    theoreticalArrivalAt: v.number(),
  }).index("by_user_operation", ["userId", "operation"]),
};
