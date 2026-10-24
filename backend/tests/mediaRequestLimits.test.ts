import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api, internal } from "../convex/_generated/api";
import schema from "../convex/schema";

// SIMULATED provider; public Convex actions and their mutations run normally.
const fake = vi.hoisted(() => ({
  configReads: 0,
  providerCreations: 0,
  creates: 0,
  gets: 0,
  signs: 0,
  deletes: 0,
  failCreate: false,
  failGet: false,
  failSign: false,
  rooms: new Map<string, { name: string; sid: string }>(),
}));

vi.mock("../convex/lib/livekitProvider", () => ({
  readLiveKitConfig: () => {
    fake.configReads += 1;
    return { serverUrl: "wss://simulated.livekit.invalid", apiUrl: "https://simulated.livekit.invalid", apiKey: "SIMULATED", apiSecret: "SIMULATED" };
  },
  createLiveKitProvider: () => {
    fake.providerCreations += 1;
    return {
      createRoom: async ({ name }: { name: string }) => {
        fake.creates += 1;
        if (fake.failCreate) throw new Error("SIMULATED_CREATE_FAILURE");
        const room = { name, sid: `RM_simulated_${fake.creates}` };
        fake.rooms.set(name, room);
        return room;
      },
      getRoom: async (name: string) => {
        fake.gets += 1;
        if (fake.failGet) throw new Error("SIMULATED_GET_FAILURE");
        return fake.rooms.get(name) ?? null;
      },
      listParticipants: async () => [],
      removeParticipant: async () => {},
      deleteRoom: async (name: string) => { fake.deletes += 1; fake.rooms.delete(name); },
    };
  },
  signJoinToken: async () => {
    fake.signs += 1;
    if (fake.failSign) throw new Error("SIMULATED_SIGN_FAILURE");
    return `SIMULATED-token-${fake.signs}`;
  },
  verifyLiveKitWebhook: async () => { throw new Error("SIMULATED_INVALID_WEBHOOK"); },
}));

const modules = import.meta.glob("../convex/**/*.ts");
const startAt = 1_800_000_000_000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(startAt);
  fake.configReads = 0; fake.providerCreations = 0; fake.creates = 0;
  fake.gets = 0; fake.signs = 0; fake.deletes = 0;
  fake.failCreate = false; fake.failGet = false; fake.failSign = false;
  fake.rooms.clear();
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

async function fixture() {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://simulated.example/", subject: "owner" });
  const helper = t.withIdentity({ issuer: "https://simulated.example/", subject: "helper" });
  const ownerUser = await owner.mutation(api.users.ensure, {});
  const helperUser = await helper.mutation(api.users.ensure, {});
  const invite = await owner.action(api.invites.createInvite, { lifetimeMs: 300_000, boundHelperId: helperUser._id });
  await helper.action(api.invites.redeemInvite, { code: invite.code });
  const session = await owner.mutation(api.sessions.start, { epoch: "simulated_epoch_00001" });
  const scope = { sessionId: session._id, epoch: session.epoch };
  const wrongScope = { ...scope, epoch: "simulated_epoch_wrong" };
  const budgets = () => t.run(ctx => ctx.db.query("mediaRequestBudgets").collect());
  return { t, owner, helper, ownerUser, helperUser, scope, wrongScope, budgets };
}

describe("durable media request budgets on public actions", () => {
  it("charges failed provisioning, rejects before provider work, and refills at the exact boundary", async () => {
    const f = await fixture();
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await expect(f.owner.action(api.livekit.provisionRoom, f.wrongScope)).rejects.toThrow("SESSION_UNAVAILABLE");
    }
    expect(fake.providerCreations).toBe(3);
    expect(fake.creates).toBe(0);
    const before = await f.budgets();
    expect(before).toMatchObject([{ userId: f.ownerUser._id, operation: "provision" }]);
    const admittedAt = before[0]!.theoreticalArrivalAt - 90_000;
    await expect(f.owner.action(api.livekit.provisionRoom, f.scope))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 30_000 } });
    expect(fake.providerCreations).toBe(3);
    expect(await f.budgets()).toEqual(before);
    const rotatedSession = await f.owner.mutation(api.sessions.start, { epoch: "simulated_epoch_rotated_02" });
    const rotatedScope = { sessionId: rotatedSession._id, epoch: rotatedSession.epoch };
    await expect(f.owner.action(api.livekit.provisionRoom, rotatedScope))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 30_000 } });
    expect(fake.providerCreations).toBe(3);
    vi.setSystemTime(admittedAt + 19_000);
    await f.owner.mutation(api.sessions.heartbeat, rotatedScope);
    vi.setSystemTime(admittedAt + 29_999);
    await expect(f.owner.action(api.livekit.provisionRoom, rotatedScope))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 1 } });
    vi.setSystemTime(admittedAt + 30_000);
    const created = await f.owner.action(api.livekit.provisionRoom, rotatedScope);
    expect(created.roomSid).toBe("RM_simulated_1");
    expect(fake.creates).toBe(1);
    expect(await f.budgets()).toHaveLength(1);
  });

  it("keeps failed provider attempts charged after the action throws", async () => {
    const f = await fixture();
    fake.failCreate = true;
    await expect(f.owner.action(api.livekit.provisionRoom, f.scope)).rejects.toThrow("MEDIA_PROVISION_UNAVAILABLE");
    expect(fake.creates).toBe(1);
    const budgets = await f.budgets();
    expect(budgets).toMatchObject([{ operation: "provision" }]);
    expect(budgets[0]!.theoreticalArrivalAt).toBeGreaterThan(Date.now());
    expect((await f.owner.query(api.sessions.get, f.scope)).cleanupPending).toBe(true);
  });

  it("rejects exhausted owner token requests before config, provider, permit or signing", async () => {
    const f = await fixture();
    await f.owner.action(api.livekit.provisionRoom, f.scope);
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await expect(f.owner.action(api.livekit.issueOwnerToken, { ...f.wrongScope, clientInstanceId: `rotating_device_${attempt}` }))
        .rejects.toThrow("SESSION_UNAVAILABLE");
    }
    const before = { configReads: fake.configReads, providerCreations: fake.providerCreations, gets: fake.gets, signs: fake.signs };
    const permits = await f.t.run(ctx => ctx.db.query("mediaTokens").collect());
    await expect(f.owner.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "new_device_instance_999" }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 10_000 } });
    expect({ configReads: fake.configReads, providerCreations: fake.providerCreations, gets: fake.gets, signs: fake.signs }).toEqual(before);
    expect(await f.t.run(ctx => ctx.db.query("mediaTokens").collect())).toEqual(permits);
    expect((await f.budgets()).filter(row => row.operation === "ownerToken")).toHaveLength(1);
    const ownerBudget = (await f.budgets()).find(row => row.operation === "ownerToken");
    if (!ownerBudget) throw new Error("Expected owner token budget");
    vi.setSystemTime(ownerBudget.theoreticalArrivalAt - 60_000 + 10_000);
    const issued = await f.owner.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "new_device_instance_999" });
    expect(issued.token).toBe("SIMULATED-token-1");
    expect(fake.gets).toBe(1);
    expect(fake.signs).toBe(1);
  });

  it("charges provider and signer failures, while account and operation budgets stay isolated", async () => {
    const f = await fixture();
    await f.owner.action(api.livekit.provisionRoom, f.scope);
    fake.failGet = true;
    await expect(f.owner.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "owner_device_0001" }))
      .rejects.toThrow("SIMULATED_GET_FAILURE");
    fake.failGet = false; fake.failSign = true;
    await expect(f.owner.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "owner_device_0002" }))
      .rejects.toThrow("SIMULATED_SIGN_FAILURE");
    const ownerTokenBudget = (await f.budgets()).find(row => row.userId === f.ownerUser._id && row.operation === "ownerToken");
    expect(ownerTokenBudget?.theoreticalArrivalAt).toBeGreaterThan(Date.now() + 19_000);
    expect((await f.budgets()).filter(row => row.operation === "provision")).toHaveLength(1);
    fake.failSign = false;
    await expect(f.helper.action(api.livekit.issueHelperToken, { ...f.wrongScope, clientInstanceId: "helper_device_0001" }))
      .rejects.toThrow("SESSION_UNAVAILABLE");
    const helperBudgets = (await f.budgets()).filter(row => row.userId === f.helperUser._id);
    expect(helperBudgets).toMatchObject([{ operation: "helperToken" }]);
    expect(helperBudgets[0]!.theoreticalArrivalAt).toBeGreaterThan(Date.now());
    await expect(f.helper.action(api.livekit.issueOwnerToken, { ...f.wrongScope, clientInstanceId: "helper_device_0002" }))
      .rejects.toThrow("SESSION_UNAVAILABLE");
    expect((await f.budgets()).filter(row => row.userId === f.helperUser._id)).toHaveLength(2);
  });

  it("charges helper token attempts to the helper and rejects exhaustion before provider or signing", async () => {
    const f = await fixture();
    await f.owner.action(api.livekit.provisionRoom, f.scope);
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await expect(f.helper.action(api.livekit.issueHelperToken, {
        ...f.wrongScope, clientInstanceId: `helper_device_instance_${attempt}`,
      })).rejects.toThrow("SESSION_UNAVAILABLE");
    }
    const before = { configReads: fake.configReads, providerCreations: fake.providerCreations, gets: fake.gets, signs: fake.signs };
    await expect(f.helper.action(api.livekit.issueHelperToken, { ...f.scope, clientInstanceId: "helper_device_instance_new" }))
      .rejects.toMatchObject({ data: { code: "RATE_LIMITED", retryAfterMs: 10_000 } });
    expect({ configReads: fake.configReads, providerCreations: fake.providerCreations, gets: fake.gets, signs: fake.signs }).toEqual(before);
    expect((await f.budgets()).filter(row => row.userId === f.helperUser._id && row.operation === "helperToken")).toHaveLength(1);
    expect((await f.budgets()).filter(row => row.userId === f.ownerUser._id && row.operation === "ownerToken")).toHaveLength(0);
    const ownerIssued = await f.owner.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "owner_device_instance_01" });
    expect(ownerIssued.token).toBe("SIMULATED-token-1");
  });

  it("serializes concurrent requests against one row and does not budget unauthenticated callers", async () => {
    const f = await fixture();
    await expect(f.t.action(api.livekit.provisionRoom, f.scope)).rejects.toThrow("Authentication required");
    expect(await f.budgets()).toHaveLength(0);
    const outcomes = await Promise.allSettled(Array.from({ length: 5 }, () =>
      f.owner.action(api.livekit.provisionRoom, f.wrongScope)));
    expect(outcomes.filter(result => result.status === "rejected" && String(result.reason).includes("SESSION_UNAVAILABLE"))).toHaveLength(3);
    expect(outcomes.filter(result => result.status === "rejected" && String(result.reason).includes("RATE_LIMITED"))).toHaveLength(2);
    expect(fake.providerCreations).toBe(3);
    expect(await f.budgets()).toMatchObject([{ operation: "provision" }]);
  });

  it("leaves End, revocation, reconciliation and cleanup available after exhaustion", async () => {
    const f = await fixture();
    const room = await f.owner.action(api.livekit.provisionRoom, f.scope);
    for (let attempt = 1; attempt < 3; attempt += 1) {
      await expect(f.owner.action(api.livekit.provisionRoom, f.wrongScope)).rejects.toThrow("SESSION_UNAVAILABLE");
    }
    await expect(f.owner.action(api.livekit.provisionRoom, f.scope)).rejects.toMatchObject({ data: { code: "RATE_LIMITED" } });
    await f.owner.mutation(api.friends.revoke, { helperId: f.helperUser._id });
    await f.t.action(internal.livekit.reconcile, { roomKey: (await f.t.run(ctx => ctx.db.query("mediaRooms").first()))!._id });
    await f.owner.mutation(api.sessions.end, f.scope);
    const cleanup = await f.t.run(ctx => ctx.db.query("mediaCleanup").first());
    expect(cleanup).toBeTruthy();
    if (!cleanup) throw new Error("Expected cleanup intent");
    await f.t.action(internal.livekit.cleanup, { cleanupId: cleanup._id });
    expect(fake.deletes).toBeGreaterThan(0);
    expect(fake.rooms.has(room.roomName)).toBe(false);
  });
});
