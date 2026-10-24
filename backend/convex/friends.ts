import { internalMutation, mutation, query } from "./_generated/server";
import type { MutationCtx, QueryCtx } from "./_generated/server";
import type { Id, Doc } from "./_generated/dataModel";
import { v } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { friendPageOptions, friendPageResult } from "./lib/friendPagination";
import { requireUser } from "./lib/auth";
import { invalidateFriendship } from "./lib/leases";

export async function requireAcceptedFriendship(
  ctx: QueryCtx | MutationCtx,
  ownerId: Id<"users">,
  helperId: Id<"users">,
): Promise<Doc<"friendships">> {
  const friendship = await ctx.db
    .query("friendships")
    .withIndex("by_owner_helper", (q) => q.eq("ownerId", ownerId).eq("helperId", helperId))
    .unique();
  if (!friendship || friendship.state !== "accepted") throw new Error("Friend access unavailable");
  return friendship;
}

export const list = query({
  args: {
    direction: v.union(v.literal("owned"), v.literal("helping")),
    state: v.union(v.literal("accepted"), v.literal("pending")),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, { direction, state, paginationOpts }) => {
    const user = await requireUser(ctx);
    const scope = { userId: user._id, view: direction, state };
    const options = friendPageOptions(paginationOpts, scope);
    const page = direction === "owned"
      ? await ctx.db.query("friendships")
        .withIndex("by_owner_state", q => q.eq("ownerId", user._id).eq("state", state))
        .order("desc").paginate(options)
      : await ctx.db.query("friendships")
        .withIndex("by_helper_state", q => q.eq("helperId", user._id).eq("state", state))
        .order("desc").paginate(options);
    return friendPageResult({
      ...page,
      page: await Promise.all(page.page.map(async friendship => {
        const counterpart = await ctx.db.get(direction === "owned" ? friendship.helperId : friendship.ownerId);
        return { friendship, counterpart: counterpart ? { id: counterpart._id, displayName: counterpart.displayName } : null };
      })),
    }, scope);
  },
});

export const pendingInvites = query({
  args: { paginationOpts: paginationOptsValidator },
  handler: async (ctx, { paginationOpts }) => {
    const owner = await requireUser(ctx);
    const scope = { userId: owner._id, view: "pendingInvites" as const };
    // Keep the index range stable across pages; filter expiry only after the bounded read.
    const page = await ctx.db.query("friendInvites")
      .withIndex("by_owner_state", q => q.eq("ownerId", owner._id).eq("state", "pending"))
      .order("desc").paginate(friendPageOptions(paginationOpts, scope));
    const now = Date.now();
    return friendPageResult({
      ...page,
      page: await Promise.all(page.page.filter(invite => invite.expiresAt > now).map(async invite => {
        const requester = invite.requestedById ? await ctx.db.get(invite.requestedById) : null;
        return { inviteId: invite._id, expiresAt: invite.expiresAt,
          requester: requester ? { id: requester._id, displayName: requester.displayName } : null };
      })),
    }, scope);
  },
});

export const approveInvite = mutation({
  args: { inviteId: v.id("friendInvites") },
  handler: async (ctx, { inviteId }) => {
    const owner = await requireUser(ctx);
    const invite = await ctx.db.get(inviteId);
    if (!invite || invite.ownerId !== owner._id || invite.state !== "pending" || invite.expiresAt <= Date.now() || !invite.requestedById) {
      throw new Error("Invitation unavailable");
    }
    const helperId = invite.requestedById;
    const friendship = await ctx.db
      .query("friendships")
      .withIndex("by_owner_helper", (q) => q.eq("ownerId", owner._id).eq("helperId", helperId))
      .unique();
    const now = Date.now();
    if (friendship) {
      await ctx.db.patch(friendship._id, {
        state: "accepted",
        notify: friendship.state === "accepted" ? friendship.notify : false,
        updatedAt: now,
      });
    } else {
      await ctx.db.insert("friendships", {
        ownerId: owner._id,
        helperId,
        state: "accepted",
        notify: false,
        createdAt: now,
        updatedAt: now,
      });
    }
    await ctx.db.patch(inviteId, { state: "accepted", acceptedAt: now });
    return { helperId };
  },
});

export const revoke = mutation({
  args: { helperId: v.id("users") },
  handler: async (ctx, { helperId }) => {
    const owner = await requireUser(ctx);
    const friendship = await ctx.db
      .query("friendships")
      .withIndex("by_owner_helper", (q) => q.eq("ownerId", owner._id).eq("helperId", helperId))
      .unique();
    const now = Date.now();
    let changed = false;
    if (friendship && friendship.state !== "revoked") {
      await ctx.db.patch(friendship._id, { state: "revoked", notify: false, updatedAt: now });
      changed = true;
    }
    const activeStates: Array<"open" | "pending"> = ["open", "pending"];
    for (const state of activeStates) {
      const invites = await ctx.db.query("friendInvites")
        .withIndex("by_owner_state_expiry", (q) => q.eq("ownerId", owner._id).eq("state", state).gt("expiresAt", now))
        .collect();
      for (const invite of invites) {
        if (invite.boundHelperId === helperId || invite.requestedById === helperId) {
          await ctx.db.patch(invite._id, { state: "revoked" });
          changed = true;
        }
      }
    }
    if (changed) await invalidateFriendship(ctx, owner._id, helperId);
    return changed;
  },
});

export const setNotifications = mutation({
  args: { ownerId: v.id("users"), enabled: v.boolean() },
  handler: async (ctx, { ownerId, enabled }) => {
    const helper = await requireUser(ctx);
    const friendship = await requireAcceptedFriendship(ctx, ownerId, helper._id);
    await ctx.db.patch(friendship._id, { notify: enabled, updatedAt: Date.now() });
    return enabled;
  },
});

const MIN_LIFETIME_MS = 5 * 60 * 1000;
const MAX_LIFETIME_MS = 7 * 24 * 60 * 60 * 1000;

export const storeInvite = internalMutation({
  args: { codeHash: v.string(), lifetimeMs: v.number(), boundHelperId: v.optional(v.id("users")) },
  handler: async (ctx, { codeHash, lifetimeMs, boundHelperId }) => {
    const owner = await requireUser(ctx);
    if (!/^[0-9a-f]{64}$/.test(codeHash) || !Number.isSafeInteger(lifetimeMs) || lifetimeMs < MIN_LIFETIME_MS || lifetimeMs > MAX_LIFETIME_MS) {
      throw new Error("Invalid invitation");
    }
    if (boundHelperId) {
      if (boundHelperId === owner._id || !(await ctx.db.get(boundHelperId))) throw new Error("Bound account unavailable");
    }
    const now = Date.now();
    let activeCount = 0;
    const activeStates: Array<"open" | "pending"> = ["open", "pending"];
    for (const state of activeStates) {
      const invites = await ctx.db.query("friendInvites")
        .withIndex("by_owner_state_expiry", (q) => q.eq("ownerId", owner._id).eq("state", state).gt("expiresAt", now))
        .take(17);
      activeCount += invites.length;
    }
    if (activeCount >= 16) throw new Error("Too many active invitations");
    const collision = await ctx.db.query("friendInvites")
      .withIndex("by_code_hash", (q) => q.eq("codeHash", codeHash)).unique();
    if (collision) throw new Error("Invitation collision; retry");
    const expiresAt = now + lifetimeMs;
    const inviteId = await ctx.db.insert("friendInvites", {
      ownerId: owner._id,
      codeHash,
      expiresAt,
      state: "open",
      boundHelperId,
      createdAt: now,
    });
    return { inviteId, expiresAt };
  },
});


export const consumeInvite = internalMutation({
  args: { codeHash: v.string() },
  handler: async (ctx, { codeHash }): Promise<{ state: "pending" | "accepted"; ownerId: Id<"users"> }> => {
    const helper = await requireUser(ctx);
    if (!/^[0-9a-f]{64}$/.test(codeHash)) throw new Error("Invitation unavailable");
    const invite = await ctx.db.query("friendInvites")
      .withIndex("by_code_hash", (q) => q.eq("codeHash", codeHash)).unique();
    const now = Date.now();
    if (!invite || invite.state !== "open" || invite.expiresAt <= now || invite.ownerId === helper._id || (invite.boundHelperId && invite.boundHelperId !== helper._id)) {
      throw new Error("Invitation unavailable");
    }
    const state: "accepted" | "pending" = invite.boundHelperId ? "accepted" : "pending";
    const existing = await ctx.db.query("friendships")
      .withIndex("by_owner_helper", (q) => q.eq("ownerId", invite.ownerId).eq("helperId", helper._id)).unique();
    if (existing) {
      if (existing.state !== "accepted") {
        await ctx.db.patch(existing._id, { state, notify: false, updatedAt: now });
      }
    } else {
      await ctx.db.insert("friendships", {
        ownerId: invite.ownerId,
        helperId: helper._id,
        state,
        notify: false,
        createdAt: now,
        updatedAt: now,
      });
    }
    const resultingState: "accepted" | "pending" = existing?.state === "accepted" ? "accepted" : state;
    await ctx.db.patch(invite._id, {
      state: resultingState,
      requestedById: helper._id,
      redeemedAt: now,
      ...(state === "accepted" || existing?.state === "accepted" ? { acceptedAt: now } : {}),
    });
    return { state: resultingState, ownerId: invite.ownerId };
  },
});
