import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convexTest } from "convex-test";
import { api, internal } from "../convex/_generated/api";
import type { Id } from "../convex/_generated/dataModel";
import schema from "../convex/schema";

const modules = import.meta.glob("../convex/**/*.ts");
const nonce = "ab0c6586-179b-456e-95af-c7edb5f268f1:1";
let nextRoom = 0;
const roomSid = "RM_sim";
function freshRoomName() {
  return `reflex-00000000-0000-4000-8000-${String(++nextRoom).padStart(12, "0")}`;
}
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_800_000_000_000); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });

async function fixture({ ready = true, authorized = true, sharing = true } = {}) {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://test.example/", subject: "owner", name: "Owner" });
  const helper = t.withIdentity({ issuer: "https://test.example/", subject: "helper" });
  const second = t.withIdentity({ issuer: "https://test.example/", subject: "second" });
  const stranger = t.withIdentity({ issuer: "https://test.example/", subject: "stranger" });
  await owner.mutation(api.users.ensure, {});
  const helperUser = await helper.mutation(api.users.ensure, {});
  const secondUser = await second.mutation(api.users.ensure, {});
  await stranger.mutation(api.users.ensure, {});
  for (const [client, user] of [[helper, helperUser], [second, secondUser]] as const) {
    const invitation = await owner.action(api.invites.createInvite, { lifetimeMs: 300_000, boundHelperId: user._id });
    await client.action(api.invites.redeemInvite, { code: invitation.code });
  }
  await owner.mutation(api.users.setAutomaticSharing, { enabled: sharing });
  const session = await owner.mutation(api.sessions.start, { epoch: "owner_session_epoch_0001" });
  const scope = { sessionId: session._id, epoch: session.epoch };
  let roomId = "";
  let roomKey: Id<"mediaRooms"> | undefined;
  const provision = async () => {
    const room = await owner.mutation(internal.mediaState.beginProvision, { ...scope, roomName: freshRoomName() });
    roomId = room.roomId;
    roomKey = room._id;
    expect(await t.mutation(internal.mediaState.finishProvision, { roomKey: room._id, roomSid })).toBe(true);
    const permit = await owner.mutation(internal.mediaState.prepareToken, {
      ...scope, role: "owner", clientInstanceId: "owner_device_instance_1", attemptId: `owner_attempt_${nextRoom}_0001`,
    });
    expect(await owner.mutation(internal.mediaState.finalizeToken, { permitId: permit.permitId, attemptId: permit.attemptId, roomSid })).toBe(true);
    const claim = await t.mutation(internal.mediaState.claimReconcile, { roomKey: room._id });
    if (!claim) throw new Error("Expected reconciliation claim");
    await t.mutation(internal.mediaState.applySnapshot, {
      roomKey: room._id, attempt: claim.reconcileAttempt, roomSid,
      participants: [{ identity: permit.identity, sid: "PI_owner", state: "active" }],
    });
    return room;
  };
  if (ready) await provision();
  if (ready && sharing && authorized) await owner.mutation(api.assistance.renewAuthorization, {
    ...scope, authorizationVersion: 1, helper: null, nonce,
  });
  const reserve = () => helper.mutation(api.assistance.reserveHelper, { ...scope, clientInstanceId: "helper_device_instance_1" });
  const connect = async () => {
    const reservation = await reserve();
    const connection = { ...scope, connectionId: reservation.connectionId, leaseVersion: reservation.leaseVersion };
    const permit = await helper.mutation(internal.mediaState.prepareToken, {
      ...scope, role: "helper", clientInstanceId: "helper_device_instance_1",
      attemptId: `helper_attempt_${reservation.leaseVersion}_0001`,
    });
    expect(await helper.mutation(internal.mediaState.finalizeToken, { permitId: permit.permitId, attemptId: permit.attemptId, roomSid })).toBe(true);
    const authorizationVersion = await t.mutation(internal.assistance.confirmConnection, {
      ...connection, authorizationVersion: reservation.reservationAuthorizationVersion,
      roomId, roomSid, participantSid: `PI_helper_${reservation.leaseVersion}`,
    });
    return { ...connection, authorizationVersion };
  };
  const finishCleanup = async (cleanupId: Id<"mediaCleanup">) => {
    const claim = await t.mutation(internal.media.claimCleanup, { cleanupId });
    if (!claim) throw new Error("Expected cleanup claim");
    await t.mutation(internal.media.finishCleanup, { cleanupId, attempt: claim.attempt, roomId: claim.roomId });
    return claim;
  };
  return { t, owner, helper, second, stranger, helperUser, secondUser, session, scope,
    get roomId() { return roomId; }, get roomKey() { return roomKey; }, provision, reserve, connect, finishCleanup };
}

describe("private sessions and discovery", () => {
  it("scopes native media status to its owner and exact epoch, with bounded remaining authority", async () => {
    const f = await fixture({ sharing: false });
    const status = await f.owner.query(api.sessions.mediaStatus, { ...f.scope, nonce });
    expect(status).toMatchObject({ sessionId: f.session._id, epoch: f.session.epoch,
      nonce, validForMs: 15_000, status: "active", sharing: false,
      media: { kind: "ready", roomSid, ownerConnected: true }, slot: { kind: "empty" } });
    await expect(f.helper.query(api.sessions.mediaStatus, { ...f.scope, nonce })).rejects.toThrow("SESSION_UNAVAILABLE");
    await expect(f.owner.query(api.sessions.mediaStatus, { ...f.scope, epoch: "wrong", nonce })).rejects.toThrow("SESSION_UNAVAILABLE");
    await expect(f.owner.query(api.sessions.mediaStatus, { ...f.scope, nonce: "bad nonce" })).rejects.toThrow("INVALID_NONCE");
    vi.advanceTimersByTime(16_000);
    expect(await f.owner.query(api.sessions.mediaStatus, { ...f.scope, nonce })).toMatchObject({ validForMs: 4_000, status: "active" });
    vi.setSystemTime(f.session.ownerExpiresAt + 1);
    expect(await f.owner.query(api.sessions.mediaStatus, { ...f.scope, nonce })).toMatchObject({ validForMs: 0, status: "ended" });
  });

  it("projects the verified helper SID without treating status as a sharing renewal", async () => {
    const f = await fixture();
    const connected = await f.connect();
    const before = await f.owner.query(api.sessions.get, f.scope);
    const status = await f.owner.query(api.sessions.mediaStatus, { ...f.scope, nonce });
    expect(status.slot).toEqual({ kind: "connected", connectionId: connected.connectionId,
      leaseVersion: connected.leaseVersion, participantSid: `PI_helper_${connected.leaseVersion}` });
    const after = await f.owner.query(api.sessions.get, f.scope);
    expect(after.sharingExpiresAt).toBe(before.sharingExpiresAt);
    expect(after.ownerExpiresAt).toBe(before.ownerExpiresAt);
    await f.owner.mutation(api.sessions.end, f.scope);
    expect(await f.owner.query(api.sessions.mediaStatus, { ...f.scope, nonce })).toMatchObject({ validForMs: 0, status: "ended" });
  });

  it("requires authenticated ownership, keeps private AI presence independent of sharing", async () => {
    const f = await fixture({ sharing: false });
    await expect(f.t.mutation(api.sessions.start, { epoch: "anonymous_session_1" })).rejects.toThrow("Authentication required");
    await expect(f.stranger.query(api.sessions.get, f.scope)).rejects.toThrow("SESSION_UNAVAILABLE");
    await expect(f.helper.mutation(api.sessions.heartbeat, f.scope)).rejects.toThrow("SESSION_UNAVAILABLE");
    await expect(f.owner.query(api.sessions.get, { ...f.scope, epoch: "wrong" })).rejects.toThrow("SESSION_UNAVAILABLE");
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    const heartbeat = await f.owner.mutation(api.sessions.heartbeat, f.scope);
    expect(heartbeat.ownerExpiresAt).toBe((await f.owner.query(api.sessions.get, f.scope)).ownerExpiresAt);
  });

  it("requires accepted directional trust, verified media readiness AND an owner sharing grant", async () => {
    const f = await fixture({ ready: false });
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
    await f.provision();
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    await f.owner.mutation(api.assistance.renewAuthorization, { ...f.scope, authorizationVersion: 1, helper: null, nonce });
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(1);
    expect((await f.stranger.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    expect((await f.owner.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    await expect(f.stranger.mutation(api.assistance.reserveHelper, { ...f.scope, clientInstanceId: "stranger_device_123" })).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("makes Start retries idempotent and never resurrects End", async () => {
    const f = await fixture();
    expect((await f.owner.mutation(api.sessions.start, { epoch: f.session.epoch }))._id).toBe(f.session._id);
    await f.owner.mutation(api.sessions.end, f.scope);
    await f.owner.mutation(api.sessions.end, f.scope);
    expect((await f.owner.mutation(api.sessions.start, { epoch: f.session.epoch })).status).toBe("ended");
    await expect(f.owner.mutation(api.sessions.heartbeat, f.scope)).rejects.toThrow("SESSION_UNAVAILABLE");
    const cleanup = await f.t.run(ctx => ctx.db.query("mediaCleanup").collect());
    expect(cleanup).toHaveLength(1);
    expect(cleanup[0]?.target.kind).toBe("room");
  });

  it("ends the previous session on a fresh Start and rejects old readiness results", async () => {
    const f = await fixture();
    const fresh = await f.owner.mutation(api.sessions.start, { epoch: "owner_session_epoch_0002" });
    expect(fresh._id).not.toBe(f.session._id);
    expect((await f.owner.query(api.sessions.get, f.scope)).status).toBe("ended");
    await expect(f.owner.mutation(internal.mediaState.beginProvision, { ...f.scope, roomName: freshRoomName() })).rejects.toThrow("SESSION_UNAVAILABLE");
    expect(fresh.media.kind).toBe("unready");
    await expect(f.owner.mutation(internal.mediaState.beginProvision, {
      sessionId: fresh._id, epoch: fresh.epoch, roomName: f.roomId,
    })).rejects.toThrow("ROOM_ALREADY_BOUND");
  });

  it("denies expired owner presence at equality before the scheduled job runs", async () => {
    const f = await fixture();
    vi.setSystemTime(f.session.ownerExpiresAt);
    await expect(f.owner.mutation(api.sessions.heartbeat, f.scope)).rejects.toThrow("SESSION_UNAVAILABLE");
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    await f.t.mutation(internal.sessions.expire, { ...f.scope, expectedExpiresAt: f.session.ownerExpiresAt });
    expect((await f.owner.query(api.sessions.get, f.scope)).endReason).toBe("owner_expired");
  });

  it("ignores an owner expiry callback after a newer heartbeat", async () => {
    const f = await fixture();
    vi.setSystemTime(Date.now() + 5_000);
    const renewed = await f.owner.mutation(api.sessions.heartbeat, f.scope);
    vi.setSystemTime(f.session.ownerExpiresAt);
    await f.t.mutation(internal.sessions.expire, { ...f.scope, expectedExpiresAt: f.session.ownerExpiresAt });
    const current = await f.owner.query(api.sessions.get, f.scope);
    expect(current.status).toBe("active");
    expect(current.ownerExpiresAt).toBe(renewed.ownerExpiresAt);
  });
});

describe("helper slot and scoped grants", () => {
  it("projects helper bootstrap and verified membership with exact owner identity and SID", async () => {
    const f = await fixture();
    const reservation = await f.reserve();
    const args = { ...f.scope, connectionId: reservation.connectionId, leaseVersion: reservation.leaseVersion, nonce };
    const reserved = await f.helper.query(api.assistance.helperStatus, args);
    expect(reserved).toMatchObject({ state: "reserved", nonce, roomName: f.roomId, roomSid,
      ownerIdentity: `owner:${f.roomKey}`, ownerParticipantSid: "PI_owner" });
    expect(reserved.validForMs).toBeGreaterThan(0);
    expect(reserved.validForMs).toBeLessThanOrEqual(15_000);
    await f.connect();
    const connected = await f.helper.query(api.assistance.helperStatus, args);
    expect(connected).toMatchObject({ state: "connected", nonce,
      participantSid: `PI_helper_${reservation.leaseVersion}`, authorizationVersion: 2 });
    const before = await f.owner.query(api.sessions.get, f.scope);
    await f.helper.query(api.assistance.helperStatus, args);
    const after = await f.owner.query(api.sessions.get, f.scope);
    expect(after.sharingExpiresAt).toBe(before.sharingExpiresAt);
    expect(after.slot).toEqual(before.slot);
  });

  it("closes viewer status on wrong identity/scope, revocation or expired authority", async () => {
    const f = await fixture();
    const connection = await f.connect();
    const args = { ...f.scope, connectionId: connection.connectionId, leaseVersion: connection.leaseVersion, nonce };
    const closed = { state: "closed", nonce, validForMs: 0 };
    await expect(f.t.query(api.assistance.helperStatus, args)).rejects.toThrow("Authentication required");
    expect(await f.second.query(api.assistance.helperStatus, args)).toEqual(closed);
    expect(await f.helper.query(api.assistance.helperStatus, { ...args, epoch: "wrong" })).toEqual(closed);
    expect(await f.helper.query(api.assistance.helperStatus, { ...args, leaseVersion: 99 })).toEqual(closed);
    await f.owner.mutation(api.friends.revoke, { helperId: f.helperUser._id });
    expect(await f.helper.query(api.assistance.helperStatus, args)).toEqual(closed);
    const expired = await fixture();
    const old = await expired.connect();
    vi.setSystemTime(Date.now() + 15_001);
    expect(await expired.helper.query(api.assistance.helperStatus, { ...expired.scope,
      connectionId: old.connectionId, leaseVersion: old.leaseVersion, nonce })).toEqual(closed);
  });

  it("serializes competing reservations and allows only the exact same-instance retry", async () => {
    const f = await fixture();
    const attempts = await Promise.allSettled([
      f.reserve(), f.second.mutation(api.assistance.reserveHelper, { ...f.scope, clientInstanceId: "helper_device_instance_2" }),
    ]);
    expect(attempts.filter(result => result.status === "fulfilled")).toHaveLength(1);
    const current = await f.owner.query(api.sessions.get, f.scope);
    if (current.slot.kind !== "reserved") throw new Error("Expected a reservation");
    const winner = current.slot.helperId === f.helperUser._id ? f.helper : f.second;
    expect(await winner.mutation(api.assistance.reserveHelper, {
      ...f.scope, clientInstanceId: current.slot.clientInstanceId,
    })).toEqual(current.slot);
    await expect(winner.mutation(api.assistance.reserveHelper, {
      ...f.scope, clientInstanceId: "another_device_instance_3",
    })).rejects.toThrow("HELPER_SLOT_OCCUPIED");
  });

  it("does not activate expired reservations or free them for rejoin without cleanup", async () => {
    const f = await fixture();
    const reservation = await f.reserve();
    vi.setSystemTime(reservation.expiresAt);
    await expect(f.t.mutation(internal.assistance.confirmConnection, {
      ...f.scope, connectionId: reservation.connectionId, leaseVersion: reservation.leaseVersion,
      authorizationVersion: 1, roomId: f.roomId, roomSid, participantSid: "PI_helper_expired",
    })).rejects.toThrow("RESERVATION_UNAVAILABLE");
    await f.t.mutation(internal.assistance.expire, { ...f.scope, connectionId: reservation.connectionId,
      leaseVersion: reservation.leaseVersion, expectedExpiresAt: reservation.expiresAt });
    const current = await f.owner.query(api.sessions.get, f.scope);
    expect(current.slot.kind).toBe("empty");
    expect(current.cleanupPending).toBe(true);
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("confirms exactly the reserved room/identity and handles duplicate verified connection", async () => {
    const f = await fixture();
    const r = await f.reserve();
    const args = { ...f.scope, connectionId: r.connectionId, leaseVersion: r.leaseVersion,
      authorizationVersion: 1, roomId: f.roomId, roomSid, participantSid: "PI_helper_1" };
    await expect(f.t.mutation(internal.assistance.confirmConnection, { ...args, roomId: "wrong-room" })).rejects.toThrow("STALE_MEDIA_RESULT");
    await expect(f.t.mutation(internal.assistance.confirmConnection, { ...args, connectionId: "forged" })).rejects.toThrow("RESERVATION_UNAVAILABLE");
    const permit = await f.helper.mutation(internal.mediaState.prepareToken, {
      ...f.scope, role: "helper", clientInstanceId: "helper_device_instance_1", attemptId: "helper_attempt_exact_1",
    });
    expect(await f.helper.mutation(internal.mediaState.finalizeToken, { permitId: permit.permitId, attemptId: permit.attemptId, roomSid })).toBe(true);
    expect(await f.t.mutation(internal.assistance.confirmConnection, args)).toBe(2);
    expect(await f.t.mutation(internal.assistance.confirmConnection, args)).toBe(2);
  });

  it("echoes the native nonce and exact scope, caps lifetime, and rejects wrong authority", async () => {
    const f = await fixture();
    const connected = await f.connect();
    const helper = { connectionId: connected.connectionId, leaseVersion: connected.leaseVersion };
    const renewal = { ...f.scope, authorizationVersion: connected.authorizationVersion, helper, nonce };
    const grant = await f.owner.mutation(api.assistance.renewAuthorization, renewal);
    expect(grant).toEqual({ scope: { sessionId: f.session._id, sessionEpoch: f.session.epoch,
      authorizationVersion: 2, helper }, nonce, validForMs: 15_000 });
    await expect(f.helper.mutation(api.assistance.renewAuthorization, renewal)).rejects.toThrow("SESSION_UNAVAILABLE");
    await expect(f.owner.mutation(api.assistance.renewAuthorization, { ...renewal, authorizationVersion: 1 })).rejects.toThrow("SHARING_UNAVAILABLE");
    await expect(f.owner.mutation(api.assistance.renewAuthorization, { ...renewal, helper: null })).rejects.toThrow("SCOPE_CHANGED");
    await expect(f.owner.mutation(api.assistance.renewAuthorization, { ...renewal, helper: { ...helper, leaseVersion: 42 } })).rejects.toThrow("CONNECTION_UNAVAILABLE");
    vi.setSystemTime((await f.owner.query(api.sessions.get, f.scope)).sharingExpiresAt - 10_000);
    expect((await f.t.mutation(internal.assistance.workerAuthorization, { ...connected, nonce })).validForMs).toBe(10_000);
  });

  it("helper heartbeat cannot renew owner permission, including with healthy owner presence", async () => {
    const f = await fixture();
    const connected = await f.connect();
    const deadline = (await f.owner.query(api.sessions.get, f.scope)).sharingExpiresAt;
    vi.setSystemTime(Date.now() + 10_000);
    await f.owner.mutation(api.sessions.heartbeat, f.scope);
    await f.helper.mutation(api.assistance.heartbeat, connected);
    const current = await f.owner.query(api.sessions.get, f.scope);
    expect(current.sharingExpiresAt).toBe(deadline);
    vi.setSystemTime(deadline);
    await expect(f.helper.mutation(api.assistance.heartbeat, connected)).rejects.toThrow("SHARING_UNAVAILABLE");
    await expect(f.t.mutation(internal.assistance.workerAuthorization, { ...connected, nonce })).rejects.toThrow("CONNECTION_UNAVAILABLE");
    await f.t.mutation(internal.sessions.expireSharing, { ...f.scope, authorizationVersion: 2, expectedExpiresAt: deadline });
    const closed = await f.owner.query(api.sessions.get, f.scope);
    expect(closed.status).toBe("active");
    expect(closed.sharing).toBe(false);
    expect(closed.cleanupPending).toBe(true);
  });

  it("owner presence alone cannot authorize a new helper after sharing expires", async () => {
    const f = await fixture();
    const deadline = (await f.owner.query(api.sessions.get, f.scope)).sharingExpiresAt;
    vi.setSystemTime(Date.now() + 10_000);
    await f.owner.mutation(api.sessions.heartbeat, f.scope);
    vi.setSystemTime(deadline);
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
    await expect(f.owner.mutation(api.assistance.renewAuthorization, { ...f.scope, authorizationVersion: 1, helper: null, nonce })).rejects.toThrow("SHARING_LEASE_EXPIRED");
    await f.owner.mutation(api.sessions.setSharing, { ...f.scope, enabled: true });
    await f.owner.mutation(api.assistance.renewAuthorization, { ...f.scope, authorizationVersion: 2, helper: null, nonce });
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(1);
  });

  it("a late verified connection cannot extend the preceding owner grant", async () => {
    const f = await fixture();
    const ownerDeadline = (await f.owner.query(api.sessions.get, f.scope)).sharingExpiresAt;
    vi.setSystemTime(ownerDeadline - 5_000);
    const connected = await f.connect();
    const current = await f.owner.query(api.sessions.get, f.scope);
    if (current.slot.kind !== "connected") throw new Error("Expected connected helper");
    expect(current.slot.authorizationExpiresAt - Date.now()).toBe(5_000);
    const workerGrant = await f.t.mutation(internal.assistance.workerAuthorization, { ...connected, nonce });
    expect(workerGrant.validForMs).toBeGreaterThan(0);
    expect(workerGrant.validForMs).toBeLessThanOrEqual(5_000);
  });
});

describe("revocation and stale callbacks", () => {
  it("rejects extra forged authority fields at the public validator boundary", async () => {
    const f = await fixture();
    const forged = { ...f.scope, ownerId: f.helperUser._id, role: "worker" };
    await expect(f.stranger.mutation(api.sessions.heartbeat, forged)).rejects.toThrow("Unexpected field");
    await expect(f.helper.mutation(api.assistance.removeHelper, { ...f.scope, helperId: f.secondUser._id })).rejects.toThrow("SESSION_UNAVAILABLE");
  });

  it("renews only with owner authority and caps grants by remaining owner presence", async () => {
    const f = await fixture();
    const connected = await f.connect();
    const original = await f.owner.query(api.sessions.get, f.scope);
    vi.setSystemTime(original.ownerExpiresAt - 7_000);
    await f.helper.mutation(api.assistance.heartbeat, connected);
    const grant = await f.owner.mutation(api.assistance.renewAuthorization, {
      ...f.scope, authorizationVersion: connected.authorizationVersion, nonce,
      helper: { connectionId: connected.connectionId, leaseVersion: connected.leaseVersion },
    });
    expect(grant.validForMs).toBeGreaterThan(6_900);
    expect(grant.validForMs).toBeLessThanOrEqual(7_000);
    const after = await f.owner.query(api.sessions.get, f.scope);
    expect(after.sharingExpiresAt).toBe(original.ownerExpiresAt);
    vi.setSystemTime(original.sharingExpiresAt);
    await f.t.mutation(internal.sessions.expireSharing, { ...f.scope, authorizationVersion: connected.authorizationVersion,
      expectedExpiresAt: original.sharingExpiresAt });
    expect((await f.owner.query(api.sessions.get, f.scope)).sharing).toBe(true);
  });

  it.each(["slot-first", "sharing-first"])("fails closed when expiry callbacks race: %s", async order => {
    const f = await fixture();
    const connection = await f.connect();
    const session = await f.owner.query(api.sessions.get, f.scope);
    vi.setSystemTime(session.sharingExpiresAt);
    const expireSlot = () => f.t.mutation(internal.assistance.expire, {
      ...f.scope, connectionId: connection.connectionId, leaseVersion: connection.leaseVersion,
      expectedExpiresAt: session.sharingExpiresAt,
    });
    const expireSharing = () => f.t.mutation(internal.sessions.expireSharing, {
      ...f.scope, authorizationVersion: session.authorizationVersion, expectedExpiresAt: session.sharingExpiresAt,
    });
    if (order === "slot-first") { await expireSlot(); await expireSharing(); }
    else { await expireSharing(); await expireSlot(); }
    const after = await f.owner.query(api.sessions.get, f.scope);
    expect(after.slot.kind).toBe("empty");
    expect(after.cleanupPending).toBe(true);
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    await expect(f.t.mutation(internal.assistance.workerAuthorization, { ...connection, nonce })).rejects.toThrow("CONNECTION_UNAVAILABLE");
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("runs scheduled expiry through the Convex test scheduler without client traffic", async () => {
    const f = await fixture();
    await f.connect();
    vi.advanceTimersByTime(20_001);
    await f.t.finishInProgressScheduledFunctions();
    const session = await f.owner.query(api.sessions.get, f.scope);
    expect(session.status).toBe("ended");
    expect(session.sharing).toBe(false);
    expect(session.cleanupPending).toBe(true);
    expect(session.slot.kind).toBe("empty");
  });

  it("revokes access between reservation and verified connection", async () => {
    const f = await fixture();
    const r = await f.reserve();
    await f.owner.mutation(api.friends.revoke, { helperId: f.helperUser._id });
    await expect(f.t.mutation(internal.assistance.confirmConnection, { ...f.scope,
      connectionId: r.connectionId, leaseVersion: r.leaseVersion, authorizationVersion: 1,
      roomId: f.roomId, roomSid, participantSid: "PI_helper_revoked" })).rejects.toThrow("STALE_MEDIA_RESULT");
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toEqual([]);
    expect((await f.owner.query(api.sessions.get, f.scope)).cleanupPending).toBe(true);
  });

  it("Stop sharing invalidates the grant and waits for media cleanup while private presence survives", async () => {
    const f = await fixture();
    const connection = await f.connect();
    await f.owner.mutation(api.sessions.setSharing, { ...f.scope, enabled: false });
    expect((await f.owner.query(api.sessions.get, f.scope)).slot.kind).toBe("empty");
    await expect(f.owner.mutation(api.sessions.setSharing, { ...f.scope, enabled: true })).rejects.toThrow("MEDIA_CLEANUP_REQUIRED");
    await expect(f.helper.mutation(api.assistance.heartbeat, connection)).rejects.toThrow("SHARING_UNAVAILABLE");
    await f.owner.mutation(api.sessions.heartbeat, f.scope);
  });

  it("session removal survives cleanup; restore does not restore revoked trust", async () => {
    const f = await fixture();
    await f.connect();
    await f.owner.mutation(api.assistance.removeHelper, { ...f.scope, helperId: f.helperUser._id });
    const cleanup = await f.t.run(ctx => ctx.db.query("mediaCleanup").first());
    if (!cleanup) throw new Error("Expected cleanup");
    await f.finishCleanup(cleanup._id);
    await f.provision();
    const current = await f.owner.query(api.sessions.get, f.scope);
    await f.owner.mutation(api.assistance.renewAuthorization, { ...f.scope, authorizationVersion: current.authorizationVersion, helper: null, nonce });
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
    await f.owner.mutation(api.assistance.restoreHelper, { ...f.scope, helperId: f.helperUser._id });
    expect((await f.helper.query(api.sessions.listJoinable, { paginationOpts: { numItems: 20, cursor: null } })).page).toHaveLength(1);
    await f.owner.mutation(api.friends.revoke, { helperId: f.helperUser._id });
    await f.owner.mutation(api.assistance.restoreHelper, { ...f.scope, helperId: f.helperUser._id });
    await expect(f.reserve()).rejects.toThrow("SHARING_UNAVAILABLE");
  });

  it("old leave/expiry/cleanup cannot release or invalidate a replacement", async () => {
    const f = await fixture();
    const connected = await f.connect();
    const old = { ...f.scope, connectionId: connected.connectionId, leaseVersion: connected.leaseVersion };
    const oldDeadline = (await f.owner.query(api.sessions.get, f.scope)).sharingExpiresAt;
    await f.helper.mutation(api.assistance.leave, old);
    const cleanup = await f.t.run(ctx => ctx.db.query("mediaCleanup").first());
    if (!cleanup) throw new Error("Expected cleanup");
    const claim = await f.t.mutation(internal.media.claimCleanup, { cleanupId: cleanup._id });
    if (!claim) throw new Error("Expected cleanup claim");
    const ack = { cleanupId: cleanup._id, roomId: cleanup.roomId, attempt: claim.attempt };
    await f.t.mutation(internal.media.finishCleanup, { ...ack, roomId: "wrong" });
    expect((await f.t.run(ctx => ctx.db.get(cleanup._id)))?.state).toBe("pending");
    await f.t.mutation(internal.media.finishCleanup, ack);
    await f.provision();
    const current = await f.owner.query(api.sessions.get, f.scope);
    await f.owner.mutation(api.assistance.renewAuthorization, { ...f.scope, authorizationVersion: current.authorizationVersion, helper: null, nonce });
    const fresh = await f.connect();
    expect(fresh.leaseVersion).toBeGreaterThan(old.leaseVersion);
    await f.helper.mutation(api.assistance.leave, old);
    await f.t.mutation(internal.assistance.expire, { ...old, expectedExpiresAt: oldDeadline });
    await f.t.mutation(internal.media.finishCleanup, ack);
    const after = await f.owner.query(api.sessions.get, f.scope);
    expect(after.slot.kind === "connected" && after.slot.connectionId).toBe(fresh.connectionId);
    await f.owner.mutation(api.assistance.removeHelper, { ...f.scope, helperId: f.helperUser._id });
    await f.t.mutation(internal.media.finishCleanup, ack);
    expect((await f.owner.query(api.sessions.get, f.scope)).cleanupPending).toBe(true);
  });
});
