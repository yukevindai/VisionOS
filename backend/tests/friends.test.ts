import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import schema from "../convex/schema";

const modules = import.meta.glob("../convex/**/*.ts");
const lifetimeMs = 5 * 60 * 1000;

afterEach(() => vi.restoreAllMocks());

describe("directional friend trust", () => {
  it("requires owner approval for an unbound code and consumes it once", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer: "https://issuer.example/", subject: "owner", name: "Owner" });
    const helper = t.withIdentity({ issuer: "https://issuer.example/", subject: "helper", name: "Helper" });
    const stranger = t.withIdentity({ issuer: "https://issuer.example/", subject: "stranger" });
    await owner.mutation(api.users.ensure, {});
    await helper.mutation(api.users.ensure, {});
    await stranger.mutation(api.users.ensure, {});

    const invite = await owner.action(api.invites.createInvite, { lifetimeMs });
    expect(invite.code).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const stored = await t.run((ctx) => ctx.db.get(invite.inviteId));
    expect(stored?.codeHash).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(stored)).not.toContain(invite.code);
    const redeemed = await helper.action(api.invites.redeemInvite, { code: invite.code });
    expect(redeemed.state).toBe("pending");
    await expect(stranger.action(api.invites.redeemInvite, { code: invite.code })).rejects.toThrow("Invitation unavailable");
    const pending = await owner.query(api.friends.list, {
      direction: "owned", state: "pending", paginationOpts: { numItems: 20, cursor: null },
    });
    expect((await owner.query(api.friends.pendingInvites, { paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(1);
    expect(pending.page[0]?.friendship.state).toBe("pending");
    expect(pending.page[0]?.friendship.notify).toBe(false);
    await expect(helper.mutation(api.friends.setNotifications, { ownerId: redeemed.ownerId, enabled: true }))
      .rejects.toThrow("Friend access unavailable");
    await expect(helper.mutation(api.friends.approveInvite, { inviteId: invite.inviteId })).rejects.toThrow("Invitation unavailable");
    await owner.mutation(api.friends.approveInvite, { inviteId: invite.inviteId });
    const accepted = await owner.query(api.friends.list, { direction: "owned", state: "accepted", paginationOpts: { numItems: 20, cursor: null } });
    expect(accepted.page[0]?.friendship.state).toBe("accepted");
    expect(accepted.page[0]?.friendship.notify).toBe(false);
    expect((await helper.query(api.friends.list, { direction: "owned", state: "accepted", paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(0);
    expect((await helper.query(api.friends.list, { direction: "helping", state: "accepted", paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(1);
    await expect(owner.mutation(api.friends.setNotifications, { ownerId: redeemed.ownerId, enabled: true }))
      .rejects.toThrow("Friend access unavailable");
    await helper.mutation(api.friends.setNotifications, { ownerId: redeemed.ownerId, enabled: true });
    expect((await owner.query(api.friends.list, { direction: "owned", state: "accepted", paginationOpts: { numItems: 20, cursor: null } })).page[0]?.friendship.notify).toBe(true);
  });

  it("binds an invitation to the exact existing account", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer: "https://issuer.example/", subject: "owner" });
    const helper = t.withIdentity({ issuer: "https://issuer.example/", subject: "helper" });
    const wrong = t.withIdentity({ issuer: "https://issuer.example/", subject: "wrong" });
    await owner.mutation(api.users.ensure, {});
    const helperUser = await helper.mutation(api.users.ensure, {});
    await wrong.mutation(api.users.ensure, {});
    const invite = await owner.action(api.invites.createInvite, { lifetimeMs, boundHelperId: helperUser._id });
    await expect(wrong.action(api.invites.redeemInvite, { code: invite.code })).rejects.toThrow("Invitation unavailable");
    expect((await helper.action(api.invites.redeemInvite, { code: invite.code })).state).toBe("accepted");
    expect((await owner.query(api.friends.list, { direction: "owned", state: "accepted", paginationOpts: { numItems: 20, cursor: null } })).page[0]?.friendship.notify).toBe(false);
    await expect(helper.action(api.invites.redeemInvite, { code: invite.code })).rejects.toThrow("Invitation unavailable");
  });

  it("revokes a bound invite before any friendship exists and on repeated revocation", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer: "https://issuer.example/", subject: "owner" });
    const helper = t.withIdentity({ issuer: "https://issuer.example/", subject: "helper" });
    await owner.mutation(api.users.ensure, {});
    const helperUser = await helper.mutation(api.users.ensure, {});

    const first = await owner.action(api.invites.createInvite, { lifetimeMs, boundHelperId: helperUser._id });
    expect(await owner.mutation(api.friends.revoke, { helperId: helperUser._id })).toBe(true);
    await expect(helper.action(api.invites.redeemInvite, { code: first.code })).rejects.toThrow("Invitation unavailable");
    expect((await t.run((ctx) => ctx.db.get(first.inviteId)))?.state).toBe("revoked");

    const second = await owner.action(api.invites.createInvite, { lifetimeMs, boundHelperId: helperUser._id });
    await helper.action(api.invites.redeemInvite, { code: second.code });
    await owner.mutation(api.friends.revoke, { helperId: helperUser._id });
    const third = await owner.action(api.invites.createInvite, { lifetimeMs, boundHelperId: helperUser._id });
    expect(await owner.mutation(api.friends.revoke, { helperId: helperUser._id })).toBe(true);
    await expect(helper.action(api.invites.redeemInvite, { code: third.code })).rejects.toThrow("Invitation unavailable");
    expect((await t.run((ctx) => ctx.db.get(third.inviteId)))?.state).toBe("revoked");
    expect(await owner.mutation(api.friends.revoke, { helperId: helperUser._id })).toBe(false);
  });

  it("rejects expired codes and revocation blocks reusing a pending approval", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer: "https://issuer.example/", subject: "owner" });
    const helper = t.withIdentity({ issuer: "https://issuer.example/", subject: "helper" });
    await owner.mutation(api.users.ensure, {});
    const helperUser = await helper.mutation(api.users.ensure, {});
    const invitation = await owner.action(api.invites.createInvite, { lifetimeMs });
    await helper.action(api.invites.redeemInvite, { code: invitation.code });
    await owner.mutation(api.friends.revoke, { helperId: helperUser._id });
    await expect(owner.mutation(api.friends.approveInvite, { inviteId: invitation.inviteId })).rejects.toThrow("Invitation unavailable");
    expect((await owner.query(api.friends.list, { direction: "owned", state: "accepted", paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(0);

    const expiring = await owner.action(api.invites.createInvite, { lifetimeMs });
    vi.spyOn(Date, "now").mockReturnValue(expiring.expiresAt + 1);
    await expect(helper.action(api.invites.redeemInvite, { code: expiring.code })).rejects.toThrow("Invitation unavailable");
  });
});
