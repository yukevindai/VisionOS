import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import type { FunctionReturnType } from "convex/server";
import { api } from "../convex/_generated/api";
import type { Doc, Id } from "../convex/_generated/dataModel";
import schema from "../convex/schema";

const modules = import.meta.glob("../convex/**/*.ts");
const now = 1_800_000_000_000;
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(now); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });
const firstPage = { paginationOpts: { numItems: 1, cursor: null } };

async function fixture() {
  const t = convexTest(schema, modules);
  const helper = t.withIdentity({ issuer: "https://simulated.example/", subject: "helper" });
  const stranger = t.withIdentity({ issuer: "https://simulated.example/", subject: "stranger" });
  const helperUser = await helper.mutation(api.users.ensure, {});
  const strangerUser = await stranger.mutation(api.users.ensure, {});
  let sequence = 0;
  const seed = async (state: "accepted" | "pending" | "revoked" = "accepted") => {
    const id = ++sequence;
    const owner = t.withIdentity({ issuer: "https://simulated.example/", subject: `owner-${id}`, name: `Owner ${id}` });
    const ownerUser = await owner.mutation(api.users.ensure, {});
    const session = await owner.mutation(api.sessions.start, { epoch: `simulated_owner_epoch_${id}` });
    // SIMULATED provider-authoritative ready state. Existing session/media tests
    // exercise real authorization transitions; these fixtures isolate discovery.
    const grantId = await t.run(async ctx => {
      await ctx.db.patch(session._id, { sharing: true, ownerExpiresAt: now + 20_000,
        sharingExpiresAt: now + 15_000, media: { kind: "ready", roomId: `simulated-room-${id}`,
          generation: 1, roomSid: `RM_simulated_${id}`, ownerConnected: true } });
      return ctx.db.insert("friendships", { ownerId: ownerUser._id, helperId: helperUser._id,
        state, notify: false, createdAt: now, updatedAt: now });
    });
    return { owner, ownerUser, session, grantId };
  };
  return { t, helper, stranger, helperUser, strangerUser, seed };
}

describe("paginated session discovery", () => {
  it("traverses only accepted friendships and projects joinable session summaries", async () => {
    const f = await fixture();
    const expected: Id<"sessions">[] = [];
    for (let i = 0; i < 7; i++) expected.push((await f.seed()).session._id);
    for (let i = 0; i < 8; i++) await f.seed(i % 2 ? "revoked" : "pending");
    let cursor: string | null = null;
    const seen: Id<"sessions">[] = [];
    let pages = 0;
    for (; pages < 10; pages++) {
      const result: FunctionReturnType<typeof api.sessions.listJoinable> = await f.helper.query(api.sessions.listJoinable, {
        paginationOpts: { numItems: 2, cursor },
      });
      expect(result.page.length).toBeLessThanOrEqual(2);
      for (const row of result.page) {
        expect(Object.keys(row).sort()).toEqual(["epoch", "ownerDisplayName", "sessionId"]);
        seen.push(row.sessionId);
      }
      if (result.isDone) break;
      expect(result.continueCursor).not.toBe(cursor);
      cursor = result.continueCursor;
    }
    expect(pages + 1).toBe(4);
    expect(seen).toHaveLength(7);
    expect(new Set(seen)).toEqual(new Set(expected));
    expect((await f.stranger.query(api.sessions.listJoinable, firstPage)).page).toEqual([]);
  });

  it("continues through an empty page without scanning all nonjoinable friends", async () => {
    const f = await fixture();
    const live = await f.seed();
    const privateOwner = await f.seed();
    await f.t.run(ctx => ctx.db.patch(privateOwner.session._id, { sharing: false }));
    const first = await f.helper.query(api.sessions.listJoinable, firstPage);
    expect(first.page).toEqual([]);
    expect(first.isDone).toBe(false);
    const second = await f.helper.query(api.sessions.listJoinable, {
      paginationOpts: { numItems: 1, cursor: first.continueCursor },
    });
    expect(second.page.map(row => row.sessionId)).toEqual([live.session._id]);
    expect(second.isDone).toBe(true);
  });

  it("rechecks friendship revocation and session removal on continuation", async () => {
    for (const change of ["revoke", "remove"] as const) {
      const f = await fixture();
      const next = await f.seed();
      await f.seed();
      const first = await f.helper.query(api.sessions.listJoinable, firstPage);
      expect(first.isDone).toBe(false);
      if (change === "revoke") await next.owner.mutation(api.friends.revoke, { helperId: f.helperUser._id });
      else await f.t.run(ctx => ctx.db.insert("sessionRemovals", {
        sessionId: next.session._id, helperId: f.helperUser._id, removed: true,
      }));
      const second = await f.helper.query(api.sessions.listJoinable, {
        paginationOpts: { numItems: 1, cursor: first.continueCursor },
      });
      expect(second.page).toEqual([]);
      expect(second.isDone).toBe(true);
    }
  });

  it("rechecks lease expiry at equality even when the original cursor is still valid", async () => {
    const f = await fixture();
    await f.seed(); await f.seed();
    const first = await f.helper.query(api.sessions.listJoinable, firstPage);
    expect(first.page).toHaveLength(1);
    vi.setSystemTime(now + 15_000);
    const second = await f.helper.query(api.sessions.listJoinable, {
      paginationOpts: { numItems: 1, cursor: first.continueCursor },
    });
    expect(second.page).toEqual([]);
    expect(second.isDone).toBe(true);
  });

  it("omits every stale, private, occupied, removed or unready session", async () => {
    const f = await fixture();
    const valid = await f.seed();
    const invalidPatches: Array<Partial<Doc<"sessions">>> = [
      { status: "ended" }, { ownerExpiresAt: now }, { sharingExpiresAt: now }, { sharing: false },
      { media: { kind: "unready" } }, { cleanupPending: true },
      { media: { kind: "ready", roomId: "simulated-offline", generation: 1, roomSid: "RM_offline", ownerConnected: false } },
      { slot: { kind: "reserved", helperId: f.strangerUser._id, clientInstanceId: "simulated_client_0001",
        connectionId: "simulated_connection_0001", leaseVersion: 1, reservationAuthorizationVersion: 1, expiresAt: now + 5_000 } },
      { slot: { kind: "connected", helperId: f.strangerUser._id, clientInstanceId: "simulated_client_0002",
        connectionId: "simulated_connection_0002", leaseVersion: 2, reservationAuthorizationVersion: 1,
        expiresAt: now + 5_000, authorizationExpiresAt: now + 5_000, participantSid: "PI_simulated" } },
    ];
    for (const patch of invalidPatches) {
      const owner = await f.seed();
      await f.t.run(ctx => ctx.db.patch(owner.session._id, patch));
    }
    const removed = await f.seed();
    await f.t.run(ctx => ctx.db.insert("sessionRemovals", { sessionId: removed.session._id, helperId: f.helperUser._id, removed: true }));
    const result = await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 50, cursor: null } });
    expect(result.page.map(row => row.sessionId)).toEqual([valid.session._id]);
    expect(result.isDone).toBe(true);
  });

  it("fails closed on duplicate active owner sessions and self-owned malformed grants", async () => {
    const f = await fixture();
    const duplicate = await f.seed();
    await f.t.run(async ctx => {
      const original = await ctx.db.get(duplicate.session._id);
      if (!original) throw new Error("Missing fixture session");
      const { _id: _discardId, _creationTime: _discardCreation, ...fields } = original;
      await ctx.db.insert("sessions", { ...fields, epoch: "simulated_duplicate_epoch" });
    });
    expect((await f.helper.query(api.sessions.listJoinable, firstPage)).page).toEqual([]);
    const own = await f.helper.mutation(api.sessions.start, { epoch: "simulated_helper_own_epoch" });
    await f.t.run(async ctx => {
      await ctx.db.patch(own._id, { sharing: true, sharingExpiresAt: now + 15_000,
        media: { kind: "ready", roomId: "simulated-self", generation: 1, roomSid: "RM_self", ownerConnected: true } });
      await ctx.db.insert("friendships", { ownerId: f.helperUser._id, helperId: f.helperUser._id,
        state: "accepted", notify: false, createdAt: now, updatedAt: now });
    });
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 50, cursor: null } })).page).toEqual([]);
  });

  it("rejects unauthenticated, cross-account/view/end cursors and invalid sizes", async () => {
    const f = await fixture();
    await f.seed(); await f.seed();
    await expect(f.t.query(api.sessions.listJoinable, firstPage)).rejects.toThrow("Authentication required");
    const first = await f.helper.query(api.sessions.listJoinable, firstPage);
    const continuation = { paginationOpts: { numItems: 1, cursor: first.continueCursor } };
    await expect(f.stranger.query(api.sessions.listJoinable, continuation)).rejects.toThrow("INVALID_CURSOR");
    await expect(f.stranger.query(api.sessions.listJoinable, {
      paginationOpts: { numItems: 1, cursor: null, endCursor: first.continueCursor },
    })).rejects.toThrow("INVALID_CURSOR");
    const friends = await f.helper.query(api.friends.list, {
      direction: "helping", state: "accepted", paginationOpts: { numItems: 1, cursor: null },
    });
    await expect(f.helper.query(api.sessions.listJoinable, {
      paginationOpts: { numItems: 1, cursor: friends.continueCursor },
    })).rejects.toThrow("INVALID_CURSOR");
    for (const numItems of [0, 51, 1.1]) {
      await expect(f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems, cursor: null } })).rejects.toThrow("INVALID_PAGE_SIZE");
    }
  });

  it("cannot gain another helper's session access by forging the cursor wrapper", async () => {
    const f = await fixture();
    await f.seed(); await f.seed();
    const first = await f.helper.query(api.sessions.listJoinable, firstPage);
    const parsed: unknown = JSON.parse(first.continueCursor);
    if (typeof parsed !== "object" || parsed === null || !("cursor" in parsed)) throw new Error("Missing fixture cursor");
    const forged = JSON.stringify({ scope: JSON.stringify([1, f.strangerUser._id, "joinableSessions"]), cursor: parsed.cursor });
    const outcome = await f.stranger.query(api.sessions.listJoinable, {
      paginationOpts: { numItems: 1, cursor: forged },
    }).then(value => ({ kind: "page", value }) as const, () => ({ kind: "rejected" }) as const);
    if (outcome.kind === "page") expect(outcome.value.page).toEqual([]);
  });
});
