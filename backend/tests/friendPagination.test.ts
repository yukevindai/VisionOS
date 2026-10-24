import { afterEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import type { FunctionReturnType } from "convex/server";
import { api } from "../convex/_generated/api";
import type { Id } from "../convex/_generated/dataModel";
import schema from "../convex/schema";
import { friendPageOptions, friendPageResult } from "../convex/lib/friendPagination";

const modules = import.meta.glob("../convex/**/*.ts");
afterEach(() => vi.restoreAllMocks());

async function fixture() {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://issuer.example/", subject: "owner", name: "Owner" });
  const helper = t.withIdentity({ issuer: "https://issuer.example/", subject: "helper", name: "Helper" });
  const stranger = t.withIdentity({ issuer: "https://issuer.example/", subject: "stranger" });
  const ownerUser = await owner.mutation(api.users.ensure, {});
  const helperUser = await helper.mutation(api.users.ensure, {});
  await stranger.mutation(api.users.ensure, {});
  const seed = (count: number, direction: "owned" | "helping", state: "accepted" | "pending" | "revoked") =>
    t.run(async ctx => {
      const ids: Id<"friendships">[] = [];
      for (let i = 0; i < count; i++) {
        const userId = await ctx.db.insert("users", { issuer: "https://fixture.example/",
          subject: `${direction}-${state}-${i}`, displayName: `Fixture ${i}`, automaticSharing: false,
          createdAt: Date.now(), updatedAt: Date.now() });
        ids.push(await ctx.db.insert("friendships", {
          ownerId: direction === "owned" ? ownerUser._id : userId,
          helperId: direction === "owned" ? userId : helperUser._id,
          state, notify: false, createdAt: Date.now(), updatedAt: Date.now(),
        }));
      }
      return ids;
    });
  return { t, owner, helper, stranger, ownerUser, helperUser, seed };
}

describe("bounded friend pagination", () => {
  it("walks accepted and pending directions without duplicates or revoked history", async () => {
    const f = await fixture();
    const owned = await f.seed(7, "owned", "accepted");
    const helping = await f.seed(5, "helping", "accepted");
    const pending = await f.seed(2, "owned", "pending");
    await f.seed(60, "owned", "revoked");
    for (const [client, direction, state, expected] of [
      [f.owner, "owned", "accepted", owned], [f.helper, "helping", "accepted", helping],
      [f.owner, "owned", "pending", pending],
    ] as const) {
      let cursor: string | null = null;
      const seen: Id<"friendships">[] = [];
      for (let pageNumber = 0; pageNumber < 10; pageNumber++) {
        const result: FunctionReturnType<typeof api.friends.list> = await client.query(api.friends.list, {
          direction, state, paginationOpts: { numItems: 2, cursor },
        });
        expect(result.page.length).toBeLessThanOrEqual(2);
        for (const row of result.page) {
          expect(row.friendship.state).toBe(state);
          expect(row.counterpart).not.toBeNull();
          expect(Object.keys(row.counterpart ?? {}).sort()).toEqual(["displayName", "id"]);
          seen.push(row.friendship._id);
        }
        if (result.isDone) break;
        expect(result.continueCursor).not.toBe(cursor);
        cursor = result.continueCursor;
      }
      expect(seen).toHaveLength(expected.length);
      expect(new Set(seen)).toEqual(new Set(expected));
    }
    expect((await f.helper.query(api.friends.list, {
      direction: "owned", state: "accepted", paginationOpts: { numItems: 10, cursor: null },
    })).page).toEqual([]);
  });

  it("authenticates every page and rejects account, view, state and end-cursor reuse", async () => {
    const f = await fixture();
    await f.seed(3, "owned", "accepted");
    const args = { direction: "owned", state: "accepted", paginationOpts: { numItems: 1, cursor: null } } as const;
    await expect(f.t.query(api.friends.list, args)).rejects.toThrow("Authentication required");
    const first = await f.owner.query(api.friends.list, args);
    const paginationOpts = { numItems: 1, cursor: first.continueCursor };
    await expect(f.stranger.query(api.friends.list, { ...args, paginationOpts })).rejects.toThrow("INVALID_CURSOR");
    await expect(f.owner.query(api.friends.list, { ...args, direction: "helping", paginationOpts })).rejects.toThrow("INVALID_CURSOR");
    await expect(f.owner.query(api.friends.list, { ...args, state: "pending", paginationOpts })).rejects.toThrow("INVALID_CURSOR");
    await expect(f.owner.query(api.friends.pendingInvites, { paginationOpts })).rejects.toThrow("INVALID_CURSOR");
    await expect(f.stranger.query(api.friends.list, { ...args,
      paginationOpts: { numItems: 1, cursor: null, endCursor: first.continueCursor },
    })).rejects.toThrow("INVALID_CURSOR");
  });

  it("rechecks auth-index isolation even if the client forges the cursor wrapper", async () => {
    const f = await fixture();
    await f.seed(3, "owned", "accepted");
    const first = await f.owner.query(api.friends.list, {
      direction: "owned", state: "accepted", paginationOpts: { numItems: 1, cursor: null },
    });
    const strangerUser = await f.stranger.mutation(api.users.ensure, {});
    const parsed: unknown = JSON.parse(first.continueCursor);
    if (typeof parsed !== "object" || parsed === null || !("cursor" in parsed)) throw new Error("Missing fixture cursor");
    const forged = JSON.stringify({ scope: JSON.stringify([1, strangerUser._id, "owned", "accepted"]), cursor: parsed.cursor });
    // The provider may reject a cursor from a different query. If accepted, the
    // authenticated index still cannot return another account's relationships.
    const result = await f.stranger.query(api.friends.list, {
      direction: "owned", state: "accepted", paginationOpts: { numItems: 1, cursor: forged },
    }).then(value => ({ kind: "page", value }) as const, () => ({ kind: "rejected" }) as const);
    if (result.kind === "page") expect(result.value.page).toEqual([]);
  });

  it("bounds sizes, rejects malformed cursors and overrides caller scan budgets", async () => {
    const f = await fixture();
    const args = { direction: "owned", state: "accepted" } as const;
    for (const numItems of [0, -1, 1.5, 51, Number.MAX_SAFE_INTEGER]) {
      await expect(f.owner.query(api.friends.list, { ...args, paginationOpts: { numItems, cursor: null } }))
        .rejects.toThrow("INVALID_PAGE_SIZE");
    }
    for (const cursor of ["not-json", "[]", "null", "42", '{"cursor":4}', "x".repeat(16_385)]) {
      await expect(f.owner.query(api.friends.list, { ...args, paginationOpts: { numItems: 1, cursor } }))
        .rejects.toThrow("INVALID_CURSOR");
    }
    const scope = { userId: f.ownerUser._id, view: "owned", state: "accepted" } as const;
    const options = friendPageOptions({ numItems: 50, cursor: null,
      maximumRowsRead: 1_000_000, maximumBytesRead: 1_000_000_000 }, scope);
    expect(options.maximumRowsRead).toBe(50);
    expect(options.maximumBytesRead).toBe(65_536);
    const page = friendPageResult({ page: [], isDone: false, continueCursor: "next", splitCursor: "split", pageStatus: "SplitRequired" }, scope);
    expect(page.pageStatus).toBe("SplitRequired");
    expect(friendPageOptions({ numItems: 2, cursor: page.splitCursor ?? null, endCursor: page.continueCursor }, scope))
      .toMatchObject({ cursor: "split", endCursor: "next" });
  });

  it("returns only live pending approvals and allows empty intermediate pages without leaking codes", async () => {
    const f = await fixture();
    const now = Date.now();
    const liveInvite = await f.t.run(async ctx => {
      const common = { ownerId: f.ownerUser._id, requestedById: f.helperUser._id, state: "pending" as const, createdAt: now };
      const live = await ctx.db.insert("friendInvites", { ...common, codeHash: "private-live-hash", expiresAt: now + 60_000 });
      await ctx.db.insert("friendInvites", { ...common, codeHash: "private-expired-hash", expiresAt: now - 1 });
      await ctx.db.insert("friendInvites", { ...common, state: "revoked", codeHash: "private-revoked-hash", expiresAt: now + 60_000 });
      return live;
    });
    const first = await f.owner.query(api.friends.pendingInvites, { paginationOpts: { numItems: 1, cursor: null } });
    expect(first.page).toEqual([]);
    expect(first.isDone).toBe(false);
    // Time changes do not change the database query's cursor identity.
    vi.spyOn(Date, "now").mockReturnValue(now + 1_000);
    const second = await f.owner.query(api.friends.pendingInvites, { paginationOpts: { numItems: 1, cursor: first.continueCursor } });
    expect(second.page).toEqual([{ inviteId: liveInvite, expiresAt: now + 60_000,
      requester: { id: f.helperUser._id, displayName: "Helper" } }]);
    expect(JSON.stringify(second)).not.toContain("private-");
    vi.spyOn(Date, "now").mockReturnValue(now + 60_000);
    expect((await f.owner.query(api.friends.pendingInvites, { paginationOpts: { numItems: 10, cursor: null } })).page).toEqual([]);
    expect((await f.stranger.query(api.friends.pendingInvites, { paginationOpts: { numItems: 10, cursor: null } })).page).toEqual([]);
    await expect(f.t.query(api.friends.pendingInvites, { paginationOpts: { numItems: 10, cursor: null } })).rejects.toThrow("Authentication required");
  });
});
