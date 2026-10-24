import { mutation, query } from "./_generated/server";
import { v } from "convex/values";
import { requireIdentity, requireUser } from "./lib/auth";

export const ensure = mutation({
  args: {},
  handler: async (ctx) => {
    const identity = await requireIdentity(ctx);
    const current = await ctx.db
      .query("users")
      .withIndex("by_auth", (q) => q.eq("issuer", identity.issuer).eq("subject", identity.subject))
      .unique();
    if (current) return current;

    const providerIdentity = await ctx.auth.getUserIdentity();
    const providerName = providerIdentity?.name?.trim();
    const displayName = providerName && providerName.length <= 80 ? providerName : "Friend";
    const now = Date.now();
    const id = await ctx.db.insert("users", {
      ...identity,
      displayName,
      automaticSharing: false,
      createdAt: now,
      updatedAt: now,
    });
    const user = await ctx.db.get(id);
    if (!user) throw new Error("Account setup failed");
    return user;
  },
});

export const me = query({
  args: {},
  handler: requireUser,
});

export const setAutomaticSharing = mutation({
  args: { enabled: v.boolean() },
  handler: async (ctx, { enabled }) => {
    const user = await requireUser(ctx);
    await ctx.db.patch(user._id, { automaticSharing: enabled, updatedAt: Date.now() });
    return enabled;
  },
});

export const setDisplayName = mutation({
  args: { displayName: v.string() },
  handler: async (ctx, { displayName }) => {
    const user = await requireUser(ctx);
    const clean = displayName.trim();
    if (clean.length < 1 || clean.length > 80) throw new Error("Display name must be 1–80 characters");
    await ctx.db.patch(user._id, { displayName: clean, updatedAt: Date.now() });
    return clean;
  },
});
