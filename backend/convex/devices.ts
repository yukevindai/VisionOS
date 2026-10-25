import { v } from "convex/values";
import { internalMutation, mutation, query } from "./_generated/server";
import { requireUser } from "./lib/auth";

// Devices an owner pairs to hear family notes (for example the demo laptop running the
// Python pipeline). Pairing happens in devicesNode:pair; the owner can list and revoke here.
// Revoking takes effect on the device's live subscription immediately.

export const MAX_DEVICES = 5;

export const list = query({
  args: {},
  handler: async (ctx) => {
    const owner = await requireUser(ctx);
    const rows = await ctx.db.query("familyDevices").withIndex("by_owner", (q) => q.eq("ownerId", owner._id)).collect();
    return rows.filter((d) => d.revokedAt === undefined)
      .map((d) => ({ deviceId: d._id, label: d.label, createdAt: d.createdAt }));
  },
});

export const revoke = mutation({
  args: { deviceId: v.id("familyDevices") },
  handler: async (ctx, { deviceId }) => {
    const owner = await requireUser(ctx);
    const device = await ctx.db.get(deviceId);
    if (!device || device.ownerId !== owner._id) throw new Error("Device not found");
    if (device.revokedAt === undefined) await ctx.db.patch(deviceId, { revokedAt: Date.now() });
    return true;
  },
});

export const store = internalMutation({
  args: { ownerId: v.id("users"), keyHash: v.string(), label: v.string() },
  handler: async (ctx, args) => {
    const active = (await ctx.db.query("familyDevices").withIndex("by_owner", (q) => q.eq("ownerId", args.ownerId)).collect())
      .filter((d) => d.revokedAt === undefined);
    if (active.length >= MAX_DEVICES) throw new Error(`At most ${MAX_DEVICES} paired devices; remove one first`);
    return ctx.db.insert("familyDevices", { ...args, createdAt: Date.now() });
  },
});
