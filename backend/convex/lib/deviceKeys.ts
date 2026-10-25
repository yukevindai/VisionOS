import type { QueryCtx, MutationCtx } from "../_generated/server";
import type { Doc } from "../_generated/dataModel";

/** Device keys look like vfn_<43 base64url chars> (32 random bytes). Only the SHA-256 hex is stored. */
export const DEVICE_KEY = /^vfn_[A-Za-z0-9_-]{43}$/;

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The paired, not-revoked device for this key, or null. Never throws, so a live subscription keeps running. */
export async function resolveDevice(ctx: QueryCtx | MutationCtx, key: string): Promise<Doc<"familyDevices"> | null> {
  if (!DEVICE_KEY.test(key)) return null;
  const keyHash = await sha256Hex(key);
  const device = await ctx.db.query("familyDevices").withIndex("by_key_hash", (q) => q.eq("keyHash", keyHash)).unique();
  return device && device.revokedAt === undefined ? device : null;
}
