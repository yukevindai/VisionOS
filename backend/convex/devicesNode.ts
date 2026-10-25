"use node";

import { createHash, randomBytes } from "node:crypto";
import { v } from "convex/values";
import { action } from "./_generated/server";
import { api, internal } from "./_generated/api";
import type { Id } from "./_generated/dataModel";

/** Owner pairs a device. The key is returned once; only its SHA-256 is stored (like invite codes). */
export const pair = action({
  args: { label: v.string() },
  handler: async (ctx, args): Promise<{ deviceId: Id<"familyDevices">; key: string }> => {
    const owner = await ctx.runQuery(api.users.me, {});
    const label = args.label.trim().replace(/\s+/g, " ");
    if (label.length === 0 || label.length > 40 || /[\u0000-\u001f\u007f]/.test(label)) throw new Error("Name the device in 1 to 40 characters");
    const key = `vfn_${randomBytes(32).toString("base64url")}`;
    const keyHash = createHash("sha256").update(key).digest("hex");
    const deviceId = await ctx.runMutation(internal.devices.store, { ownerId: owner._id, keyHash, label });
    return { deviceId, key };
  },
});
