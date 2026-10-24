import { defineTable } from "convex/server";
import { v } from "convex/values";

export const mediaRequestLimitTables = {
  mediaRequestBudgets: defineTable({
    userId: v.id("users"),
    operation: v.union(v.literal("provision"), v.literal("ownerToken"), v.literal("helperToken")),
    theoreticalArrivalAt: v.number(),
  }).index("by_user_operation", ["userId", "operation"]),
};
