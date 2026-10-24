import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api } from "../convex/_generated/api";
import schema from "../convex/schema";

const modules = import.meta.glob("../convex/**/*.ts");
const issuer = "https://issuer.example/";
const lifetimeMs = 300_000;

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("durable invitation attempt limits", () => {
  it("charges rejected creation and exposes the exact create refill boundary", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer, subject: "owner" });
    const user = await owner.mutation(api.users.ensure, {});

    await expect(owner.action(api.invites.createInvite, { lifetimeMs: "invalid" }))
      .rejects.toThrow("Invitation lifetime");
    for (let attempt = 1; attempt < 5; attempt += 1) {
      await expect(owner.action(api.invites.createInvite, { lifetimeMs: 1 }))
        .rejects.toThrow("Invitation lifetime");
    }
    await expect(owner.action(api.invites.createInvite, { lifetimeMs }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
    vi.setSystemTime(1_059_999);
    await expect(owner.action(api.invites.createInvite, { lifetimeMs }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 1 } });
    vi.setSystemTime(1_060_000);
    const invited = await owner.action(api.invites.createInvite, { lifetimeMs });
    expect(invited.code).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const budgets = await t.run(ctx => ctx.db.query("inviteAttemptBudgets")
      .withIndex("by_user_operation", q => q.eq("userId", user._id).eq("operation", "create")).collect());
    expect(budgets).toHaveLength(1);
    expect(JSON.stringify(budgets)).not.toContain(invited.code);
  });

  it("charges malformed, unknown, expired, and wrong-bound redemption before failure", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer, subject: "owner" });
    const helper = t.withIdentity({ issuer, subject: "helper" });
    const intended = t.withIdentity({ issuer, subject: "intended" });
    await owner.mutation(api.users.ensure, {});
    const helperUser = await helper.mutation(api.users.ensure, {});
    const intendedUser = await intended.mutation(api.users.ensure, {});
    const wrongBound = await owner.action(api.invites.createInvite, { lifetimeMs, boundHelperId: intendedUser._id });
    const expired = await owner.action(api.invites.createInvite, { lifetimeMs });
    await t.run(ctx => ctx.db.patch(expired.inviteId, { expiresAt: 1 }));

    await expect(helper.action(api.invites.redeemInvite, { code: "bad" })).rejects.toThrow("Invitation unavailable");
    await expect(helper.action(api.invites.redeemInvite, { code: 42 })).rejects.toThrow("Invitation unavailable");
    await expect(helper.action(api.invites.redeemInvite, { code: "A".repeat(43) })).rejects.toThrow("Invitation unavailable");
    await expect(helper.action(api.invites.redeemInvite, { code: expired.code })).rejects.toThrow("Invitation unavailable");
    await expect(helper.action(api.invites.redeemInvite, { code: wrongBound.code })).rejects.toThrow("Invitation unavailable");
    const budgets = await t.run(ctx => ctx.db.query("inviteAttemptBudgets")
      .withIndex("by_user_operation", q => q.eq("userId", helperUser._id).eq("operation", "redeem")).collect());
    expect(budgets).toHaveLength(1);
    expect(budgets[0]?.theoreticalArrivalAt).toBeGreaterThan(Date.now());
    expect(JSON.stringify(budgets)).not.toContain(wrongBound.code);

    for (let attempt = 5; attempt < 10; attempt += 1) {
      await expect(helper.action(api.invites.redeemInvite, { code: "bad" })).rejects.toThrow("Invitation unavailable");
    }
    await expect(helper.action(api.invites.redeemInvite, { code: wrongBound.code }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
    expect((await t.run(ctx => ctx.db.get(wrongBound.inviteId)))?.state).toBe("open");
  });

  it("refills one redeem attempt exactly at ten seconds", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(2_000_000);
    const t = convexTest(schema, modules);
    const helper = t.withIdentity({ issuer, subject: "helper" });
    await helper.mutation(api.users.ensure, {});
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await expect(helper.action(api.invites.redeemInvite, { code: "bad" }))
        .rejects.toThrow("Invitation unavailable");
    }
    await expect(helper.action(api.invites.redeemInvite, { code: "bad" }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
    vi.setSystemTime(2_009_999);
    await expect(helper.action(api.invites.redeemInvite, { code: "bad" }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 1 } });
    vi.setSystemTime(2_010_000);
    await expect(helper.action(api.invites.redeemInvite, { code: "bad" }))
      .rejects.toThrow("Invitation unavailable");
    await expect(helper.action(api.invites.redeemInvite, { code: "bad" }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
  });

  it("isolates accounts and operations, and requires a verified setup account", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer, subject: "owner" });
    const helper = t.withIdentity({ issuer, subject: "helper" });
    const unregistered = t.withIdentity({ issuer, subject: "unregistered" });
    await owner.mutation(api.users.ensure, {});
    await helper.mutation(api.users.ensure, {});

    await expect(t.action(api.invites.redeemInvite, { code: "bad" })).rejects.toThrow("Authentication required");
    await expect(unregistered.action(api.invites.createInvite, { lifetimeMs })).rejects.toThrow("Account setup required");
    await expect(unregistered.action(api.invites.redeemInvite, { code: "bad" })).rejects.toThrow("Account setup required");
    expect(await t.run(ctx => ctx.db.query("inviteAttemptBudgets").collect())).toHaveLength(0);

    for (let attempt = 0; attempt < 10; attempt += 1) {
      await expect(helper.action(api.invites.redeemInvite, { code: "bad" })).rejects.toThrow("Invitation unavailable");
    }
    await expect(helper.action(api.invites.redeemInvite, { code: "bad" }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
    expect((await helper.action(api.invites.createInvite, { lifetimeMs })).code).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect((await owner.action(api.invites.createInvite, { lifetimeMs })).code).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(await t.run(ctx => ctx.db.query("inviteAttemptBudgets").collect())).toHaveLength(3);
  });

  it("does not block revocation after invitation budgets are exhausted", async () => {
    const t = convexTest(schema, modules);
    const owner = t.withIdentity({ issuer, subject: "owner" });
    const helper = t.withIdentity({ issuer, subject: "helper" });
    const ownerUser = await owner.mutation(api.users.ensure, {});
    const helperUser = await helper.mutation(api.users.ensure, {});
    const invitation = await owner.action(api.invites.createInvite, { lifetimeMs, boundHelperId: helperUser._id });
    await helper.action(api.invites.redeemInvite, { code: invitation.code });
    await helper.mutation(api.friends.setNotifications, { ownerId: ownerUser._id, enabled: true });
    for (let attempt = 1; attempt < 5; attempt += 1) {
      await expect(owner.action(api.invites.createInvite, { lifetimeMs: 1 })).rejects.toThrow("Invitation lifetime");
    }
    await expect(owner.action(api.invites.createInvite, { lifetimeMs })).rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
    expect(await owner.mutation(api.friends.revoke, { helperId: helperUser._id })).toBe(true);
    const friendship = await t.run(ctx => ctx.db.query("friendships")
      .withIndex("by_owner_helper", q => q.eq("ownerId", ownerUser._id).eq("helperId", helperUser._id)).unique());
    expect(friendship).toMatchObject({ state: "revoked", notify: false });
  });

  it("serializes concurrent redemption attempts against one bounded row", async () => {
    const t = convexTest(schema, modules);
    const helper = t.withIdentity({ issuer, subject: "helper" });
    const user = await helper.mutation(api.users.ensure, {});
    const outcomes = await Promise.allSettled(Array.from({ length: 12 }, () =>
      helper.action(api.invites.redeemInvite, { code: "bad" })));
    expect(outcomes.filter(result => result.status === "rejected" &&
      String(result.reason).includes("Invitation unavailable"))).toHaveLength(10);
    expect(outcomes.filter(result => result.status === "rejected" &&
      String(result.reason).includes("RATE_LIMITED"))).toHaveLength(2);
    const budgets = await t.run(ctx => ctx.db.query("inviteAttemptBudgets")
      .withIndex("by_user_operation", q => q.eq("userId", user._id).eq("operation", "redeem")).collect());
    expect(budgets).toHaveLength(1);
  });
});
