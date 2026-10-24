import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api, internal } from "../convex/_generated/api";
import type { Id } from "../convex/_generated/dataModel";
import schema from "../convex/schema";

// SIMULATED LiveKit transport. Convex actions and mutations below are real.
const fake = vi.hoisted(() => ({
  rooms: new Map<string, { name: string; sid: string }>(),
  participants: new Map<string, Array<{ identity: string; sid: string; state: "active" | "other" }>>(),
  createWait: undefined as Promise<void> | undefined,
  signWait: undefined as Promise<void> | undefined,
  failCreate: false,
  failGet: false,
  failSign: false,
  failDelete: false,
  stickyDelete: false,
  signed: [] as Array<{ role: string; identity: string; roomName: string }>,
}));

vi.mock("../convex/lib/livekitProvider", () => ({
  readLiveKitConfig: () => ({ serverUrl: "wss://simulated.livekit.invalid", apiUrl: "https://simulated.livekit.invalid", apiKey: "SIMULATED", apiSecret: "SIMULATED" }),
  createLiveKitProvider: () => ({
    createRoom: async ({ name }: { name: string }) => {
      await fake.createWait;
      if (fake.failCreate) throw new Error("SIMULATED_PROVIDER_CREATE_FAILURE");
      const room = { name, sid: `RM_${fake.rooms.size + 1}` };
      fake.rooms.set(name, room);
      return room;
    },
    getRoom: async (name: string) => {
      if (fake.failGet) throw new Error("SIMULATED_PROVIDER_READ_FAILURE");
      return fake.rooms.get(name) ?? null;
    },
    listParticipants: async (name: string) => fake.participants.get(name) ?? [],
    removeParticipant: async ({ roomName, identity }: { roomName: string; identity: string }) => {
      fake.participants.set(roomName, (fake.participants.get(roomName) ?? []).filter(p => p.identity !== identity));
    },
    deleteRoom: async (name: string) => {
      if (fake.failDelete) throw new Error("SIMULATED_PROVIDER_DELETE_FAILURE");
      if (!fake.stickyDelete) fake.rooms.delete(name);
    },
  }),
  signJoinToken: async (input: { role: string; identity: string; roomName: string }) => {
    await fake.signWait;
    if (fake.failSign) throw new Error("SIMULATED_SIGNER_FAILURE");
    fake.signed.push(input);
    return `SIMULATED-token:${input.role}:${input.identity}`;
  },
  verifyLiveKitWebhook: async ({ body, authorization }: { body: string; authorization: string }) => {
    if (authorization !== "SIMULATED-signed") throw new Error("SIMULATED_BAD_SIGNATURE");
    const event: unknown = JSON.parse(body);
    if (typeof event !== "object" || event === null || !("id" in event)) throw new Error("SIMULATED_BAD_EVENT");
    return event;
  },
}));

const modules = import.meta.glob("../convex/**/*.ts");
const nonce = "ab0c6586-179b-456e-95af-c7edb5f268f1:1";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(1_800_000_000_000);
  fake.rooms.clear(); fake.participants.clear(); fake.signed.length = 0;
  fake.createWait = undefined; fake.signWait = undefined;
  fake.failCreate = false; fake.failGet = false; fake.failSign = false; fake.failDelete = false; fake.stickyDelete = false;
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

async function fixture({ sharing = true, trust = true } = {}) {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://test.example/", subject: "owner", name: "Owner" });
  const helper = t.withIdentity({ issuer: "https://test.example/", subject: "helper" });
  const stranger = t.withIdentity({ issuer: "https://test.example/", subject: "stranger" });
  await owner.mutation(api.users.ensure, {});
  const helperUser = await helper.mutation(api.users.ensure, {});
  await stranger.mutation(api.users.ensure, {});
  if (trust) {
    const invitation = await owner.action(api.invites.createInvite, { lifetimeMs: 300_000, boundHelperId: helperUser._id });
    await helper.action(api.invites.redeemInvite, { code: invitation.code });
  }
  await owner.mutation(api.users.setAutomaticSharing, { enabled: sharing });
  const session = await owner.mutation(api.sessions.start, { epoch: "owner_session_epoch_0001" });
  const scope = { sessionId: session._id, epoch: session.epoch };
  const roomKey = async (): Promise<Id<"mediaRooms">> => {
    const room = (await t.run(ctx => ctx.db.query("mediaRooms").collect())).find(candidate => candidate.sessionId === session._id);
    if (!room) throw new Error("Expected media room");
    return room._id;
  };
  const provision = () => owner.action(api.livekit.provisionRoom, scope);
  const ownerToken = () => owner.action(api.livekit.issueOwnerToken, { ...scope, clientInstanceId: "owner_device_instance_1" });
  const helperToken = () => helper.action(api.livekit.issueHelperToken, { ...scope, clientInstanceId: "helper_device_instance_1" });
  const ownerPresent = async () => {
    const issued = await ownerToken();
    fake.participants.set(issued.roomName, [{ identity: issued.identity, sid: "PI_owner", state: "active" }]);
    await t.action(internal.livekit.reconcile, { roomKey: await roomKey() });
    return issued;
  };
  const grant = () => owner.mutation(api.assistance.renewAuthorization, { ...scope, authorizationVersion: 1, helper: null, nonce });
  const reserve = () => helper.mutation(api.assistance.reserveHelper, { ...scope, clientInstanceId: "helper_device_instance_1" });
  const cleanupId = async (): Promise<Id<"mediaCleanup">> => {
    const job = await t.run(ctx => ctx.db.query("mediaCleanup").first());
    if (!job) throw new Error("Expected cleanup intent");
    return job._id;
  };
  return { t, owner, helper, stranger, helperUser, session, scope, provision, roomKey, ownerToken, helperToken, ownerPresent, grant, reserve, cleanupId };
}

describe("SIMULATED LiveKit action orchestration", () => {
  it("denies signed-out and unrelated callers at public actions", async () => {
    const f = await fixture();
    await expect(f.t.action(api.livekit.provisionRoom, f.scope)).rejects.toThrow("Authentication required");
    await expect(f.stranger.action(api.livekit.provisionRoom, f.scope)).rejects.toThrow("SESSION_UNAVAILABLE");
    await f.provision();
    await expect(f.t.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "stranger_device_1" })).rejects.toThrow("Authentication required");
    await expect(f.stranger.action(api.livekit.issueOwnerToken, { ...f.scope, clientInstanceId: "stranger_device_1" })).rejects.toThrow("SESSION_UNAVAILABLE");
  });

  it("issues a private owner token with sharing off and rejects second issuance", async () => {
    const f = await fixture({ sharing: false });
    const room = await f.provision();
    const token = await f.ownerToken();
    expect(token).toMatchObject({ roomName: room.roomName, roomSid: room.roomSid, token: expect.stringContaining("SIMULATED-token:owner:") });
    expect(fake.signed).toMatchObject([{ role: "owner", identity: token.identity, roomName: room.roomName }]);
    await expect(f.ownerToken()).rejects.toThrow("TOKEN_ALREADY_ISSUED");
    await expect(f.helperToken()).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("rejects role spoof fields and helper issuance before reservation or verified owner presence", async () => {
    const f = await fixture();
    await f.provision();
    await expect(f.owner.action(api.livekit.issueOwnerToken, {
      // @ts-expect-error Exercise runtime rejection of a forged extra field.
      ...f.scope, clientInstanceId: "owner_device_instance_1", role: "worker",
    })).rejects.toThrow("Unexpected field");
    await expect(f.helperToken()).rejects.toThrow("SHARING_UNAVAILABLE");
    await f.ownerToken();
    await expect(f.helperToken()).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("issues helper only after trust, owner reconciliation, owner grant and exact reservation", async () => {
    const f = await fixture();
    await f.provision();
    await f.ownerPresent();
    await expect(f.helperToken()).rejects.toThrow("SHARING_UNAVAILABLE");
    await f.grant();
    await expect(f.helperToken()).rejects.toThrow("RESERVATION_UNAVAILABLE");
    const reservation = await f.reserve();
    const issued = await f.helperToken();
    expect(issued.identity).toBe(reservation.connectionId);
    expect(issued.token).toContain("SIMULATED-token:helper:");
    await expect(f.helper.action(api.livekit.issueHelperToken, { ...f.scope, clientInstanceId: "wrong_device_instance_1" })).rejects.toThrow("RESERVATION_UNAVAILABLE");
    await expect(f.helperToken()).rejects.toThrow("TOKEN_ALREADY_ISSUED");
  });

  it("does not issue to a stranger even when the owner is ready and sharing", async () => {
    const f = await fixture({ trust: false });
    await f.provision(); await f.ownerPresent(); await f.grant();
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
    await expect(f.helperToken()).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("rejects a delayed token after revocation between prepare and finalize", async () => {
    const f = await fixture();
    await f.provision(); await f.ownerPresent(); await f.grant(); await f.reserve();
    let release = () => {};
    fake.signWait = new Promise<void>(resolve => { release = resolve; });
    const pending = f.helperToken();
    await vi.waitFor(async () => {
      const permits = await f.t.run(ctx => ctx.db.query("mediaTokens").collect());
      expect(permits.some(p => p.role === "helper" && p.state === "prepared")).toBe(true);
    });
    await f.owner.mutation(api.friends.revoke, { helperId: f.helperUser._id });
    release();
    await expect(pending).rejects.toThrow("MEDIA_AUTHORIZATION_CHANGED");
    const permits = await f.t.run(ctx => ctx.db.query("mediaTokens").collect());
    expect(permits.find(p => p.role === "helper")?.state).toBe("prepared");
  });

  it("does not release a token after simulated signer failure", async () => {
    const f = await fixture({ sharing: false });
    await f.provision();
    fake.failSign = true;
    await expect(f.ownerToken()).rejects.toThrow("SIMULATED_SIGNER_FAILURE");
    expect(fake.signed).toEqual([]);
    const permit = await f.t.run(ctx => ctx.db.query("mediaTokens").first());
    expect(permit?.state).toBe("prepared");
  });

  it("does not release a token after simulated provider read failure", async () => {
    const f = await fixture({ sharing: false });
    await f.provision();
    fake.failGet = true;
    await expect(f.ownerToken()).rejects.toThrow("SIMULATED_PROVIDER_READ_FAILURE");
    expect(fake.signed).toEqual([]);
    const permit = await f.t.run(ctx => ctx.db.query("mediaTokens").first());
    expect(permit?.state).toBe("prepared");
  });

  it("retires a late room creation after End", async () => {
    const f = await fixture();
    let release = () => {};
    fake.createWait = new Promise<void>(resolve => { release = resolve; });
    const pending = f.provision();
    await vi.waitFor(async () => expect(await f.t.run(ctx => ctx.db.query("mediaRooms").first())).toBeTruthy());
    await f.owner.mutation(api.sessions.end, f.scope);
    release();
    await expect(pending).rejects.toThrow("MEDIA_PROVISION_UNAVAILABLE");
    expect((await f.t.run(ctx => ctx.db.query("mediaCleanup").collect())).length).toBeGreaterThan(0);
  });

  it("reconciliation detects a missing room after a signed webhook without trusting event contents", async () => {
    const f = await fixture();
    const room = await f.provision();
    await f.ownerPresent(); await f.grant();
    fake.rooms.delete(room.roomName);
    const body = JSON.stringify({ id: "EV_missing_room", event: "participant_left", createdAt: 1_800_000_000, room: { name: room.roomName, sid: room.roomSid } });
    expect(await f.t.action(internal.livekit.receiveWebhook, { body, authorization: "SIMULATED-signed" })).toBe(204);
    vi.advanceTimersByTime(5_001);
    await f.t.action(internal.livekit.reconcile, { roomKey: await f.roomKey() });
    const current = await f.owner.query(api.sessions.get, f.scope);
    expect(current.cleanupPending).toBe(true);
    expect(current.media.kind).toBe("retired");
  });

  it("retries provider cleanup failure and ignores stale attempt completion", async () => {
    const f = await fixture();
    await f.provision(); await f.owner.mutation(api.sessions.end, f.scope);
    const cleanupId = await f.cleanupId();
    fake.failDelete = true;
    await f.t.action(internal.livekit.cleanup, { cleanupId });
    const retry = await f.t.run(ctx => ctx.db.get(cleanupId));
    expect(retry).toMatchObject({ state: "pending", attempt: 1, lastFailure: "provider_unavailable_or_room_present" });
    fake.failDelete = false;
    vi.advanceTimersByTime(2_001);
    const claim = await f.t.mutation(internal.media.claimCleanup, { cleanupId });
    if (!claim) throw new Error("Expected second cleanup claim");
    await f.t.mutation(internal.media.finishCleanup, { cleanupId, attempt: 1, roomId: claim.roomId });
    expect((await f.t.run(ctx => ctx.db.get(cleanupId)))?.state).toBe("pending");
    await f.t.mutation(internal.media.finishCleanup, { cleanupId, attempt: claim.attempt, roomId: claim.roomId });
    expect((await f.t.run(ctx => ctx.db.get(cleanupId)))?.state).toBe("verified");
  });

  it("keeps cleanup pending when delete succeeds but a room remains", async () => {
    const f = await fixture();
    const room = await f.provision(); await f.owner.mutation(api.sessions.end, f.scope);
    fake.stickyDelete = true;
    const cleanupId = await f.cleanupId();
    await f.t.action(internal.livekit.cleanup, { cleanupId });
    expect(fake.rooms.has(room.roomName)).toBe(true);
    expect((await f.t.run(ctx => ctx.db.get(cleanupId)))?.state).toBe("pending");
  });

  it("deduplicates signed webhooks and audits a recreated retired room", async () => {
    const f = await fixture();
    const room = await f.provision(); await f.owner.mutation(api.sessions.end, f.scope);
    const cleanupId = await f.cleanupId();
    await f.t.action(internal.livekit.cleanup, { cleanupId });
    expect((await f.t.run(ctx => ctx.db.get(cleanupId)))?.state).toBe("verified");
    const body = JSON.stringify({ id: "EV_retired_room", event: "room_started", createdAt: 1_800_000_000, room: { name: room.roomName, sid: room.roomSid } });
    expect(await f.t.action(internal.livekit.receiveWebhook, { body, authorization: "SIMULATED-signed" })).toBe(204);
    expect(await f.t.action(internal.livekit.receiveWebhook, { body, authorization: "SIMULATED-signed" })).toBe(204);
    expect((await f.t.run(ctx => ctx.db.query("mediaEvents").collect()))).toHaveLength(1);
    expect((await f.t.run(ctx => ctx.db.query("mediaCleanup").collect()))).toHaveLength(2);
    const second = (await f.t.run(ctx => ctx.db.query("mediaCleanup").collect())).find(job => job._id !== cleanupId);
    if (!second) throw new Error("Expected retired event cleanup");
    await f.t.action(internal.livekit.cleanup, { cleanupId: second._id });
    expect((await f.t.run(ctx => ctx.db.get(second._id)))?.state).toBe("verified");
    // SIMULATED cached-token room recreation after a successful deletion.
    fake.rooms.set(room.roomName, { name: room.roomName, sid: "RM_recreated" });
    vi.advanceTimersByTime(60_001);
    await f.t.mutation(internal.media.auditRetired, {});
    const jobs = await f.t.run(ctx => ctx.db.query("mediaCleanup").collect());
    expect(jobs).toHaveLength(3);
    expect(jobs.filter(job => job.state === "pending")).toHaveLength(1);
  });
});
