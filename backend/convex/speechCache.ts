import { v } from "convex/values";
import { internalMutation, internalQuery } from "./_generated/server";

export const get = internalQuery({
  args: { key: v.string() },
  handler: async (ctx, { key }) => {
    const row = await ctx.db.query("speechCache").withIndex("by_key", (q) => q.eq("key", key)).unique();
    return row?.storageId ?? null;
  },
});

export const put = internalMutation({
  args: { key: v.string(), storageId: v.id("_storage"), characters: v.number() },
  handler: async (ctx, { key, storageId, characters }) => {
    const existing = await ctx.db.query("speechCache").withIndex("by_key", (q) => q.eq("key", key)).unique();
    if (existing) {
      // A concurrent request already cached this audio; drop the duplicate blob.
      await ctx.storage.delete(storageId);
      return;
    }
    await ctx.db.insert("speechCache", { key, storageId, characters, createdAt: Date.now() });
  },
});

export const remove = internalMutation({
  args: { key: v.string() },
  handler: async (ctx, { key }) => {
    const row = await ctx.db.query("speechCache").withIndex("by_key", (q) => q.eq("key", key)).unique();
    if (row) await ctx.db.delete(row._id);
  },
});
