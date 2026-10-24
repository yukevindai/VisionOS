import type { MutationCtx, QueryCtx } from "../_generated/server";
import type { Doc } from "../_generated/dataModel";

export async function requireIdentity(ctx: QueryCtx | MutationCtx) {
  const identity = await ctx.auth.getUserIdentity();
  if (!identity || !identity.issuer || !identity.subject) {
    throw new Error("Authentication required");
  }
  return { issuer: identity.issuer, subject: identity.subject };
}

export async function requireUser(ctx: QueryCtx | MutationCtx): Promise<Doc<"users">> {
  const identity = await requireIdentity(ctx);
  const user = await ctx.db
    .query("users")
    .withIndex("by_auth", (q) => q.eq("issuer", identity.issuer).eq("subject", identity.subject))
    .unique();
  if (!user) {
    throw new Error("Account setup required");
  }
  return user;
}
