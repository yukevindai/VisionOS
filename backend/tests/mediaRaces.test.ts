import { createHash } from "node:crypto";
import { AccessToken } from "livekit-server-sdk";
import { convexTest } from "convex-test";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, internal } from "../convex/_generated/api";
import schema from "../convex/schema";

const modules = import.meta.glob("../convex/**/*.ts");
const roomName = "reflex-00000000-0000-4000-8000-000000000001";
const roomSid = "RM_simulated";
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_800_000_000_000); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllEnvs(); });

async function setup() {
  const t = convexTest(schema, modules);
  const owner = t.withIdentity({ issuer: "https://simulated.example/", subject: "owner" });
  const helper = t.withIdentity({ issuer: "https://simulated.example/", subject: "helper" });
  await owner.mutation(api.users.ensure, {});
  const friend = await helper.mutation(api.users.ensure, {});
  const invite = await owner.action(api.invites.createInvite, { lifetimeMs: 300_000, boundHelperId: friend._id });
  await helper.action(api.invites.redeemInvite, { code: invite.code });
  await owner.mutation(api.users.setAutomaticSharing, { enabled: true });
  const session = await owner.mutation(api.sessions.start, { epoch: "simulated_epoch_00001" });
  const scope = { sessionId: session._id, epoch: session.epoch };
  const room = await owner.mutation(internal.mediaState.beginProvision, { ...scope, roomName });
  await t.mutation(internal.mediaState.finishProvision, { roomKey: room._id, roomSid });
  const permit = await owner.mutation(internal.mediaState.prepareToken, {
    ...scope, role: "owner", clientInstanceId: "owner_device_00001", attemptId: "owner_attempt_00001",
  });
  await owner.mutation(internal.mediaState.finalizeToken, { permitId: permit.permitId, attemptId: permit.attemptId, roomSid });
  const ownerMember = { identity: permit.identity, sid: "PI_owner", state: "active" as const };
  const claim = await t.mutation(internal.mediaState.claimReconcile, { roomKey: room._id });
  if (!claim) throw new Error("Missing claim");
  await t.mutation(internal.mediaState.applySnapshot, { roomKey: room._id, attempt: claim.reconcileAttempt, roomSid, participants: [ownerMember] });
  const reserve = async () => {
    await owner.mutation(api.assistance.renewAuthorization, { ...scope, authorizationVersion: 1, helper: null, nonce: "simulated-renewal:1" });
    const slot = await helper.mutation(api.assistance.reserveHelper, { ...scope, clientInstanceId: "helper_device_00001" });
    const token = await helper.mutation(internal.mediaState.prepareToken, {
      ...scope, role: "helper", clientInstanceId: "helper_device_00001", attemptId: "helper_attempt_00001",
    });
    await helper.mutation(internal.mediaState.finalizeToken, { permitId: token.permitId, attemptId: token.attemptId, roomSid });
    return { identity: slot.connectionId, sid: "PI_helper", state: "active" as const };
  };
  const snapshot = async (participants: Array<{ identity: string; sid: string; state: "active" | "other" }>) => {
    vi.setSystemTime(Date.now() + 5_001);
    const next = await t.mutation(internal.mediaState.claimReconcile, { roomKey: room._id });
    if (!next) throw new Error("Missing next claim");
    await t.mutation(internal.mediaState.applySnapshot, { roomKey: room._id, attempt: next.reconcileAttempt, roomSid, participants });
  };
  return { t, owner, helper, room, scope, ownerMember, reserve, snapshot };
}

it("same-SID provisioning completion stays idempotent after authorization changes", async () => {
  const f = await setup();
  await f.owner.mutation(api.sessions.setSharing, { ...f.scope, enabled: false });
  const changed = await f.owner.query(api.sessions.get, f.scope);
  expect(changed.authorizationVersion).toBe(2);
  expect(await f.t.mutation(internal.mediaState.finishProvision, { roomKey: f.room._id, roomSid })).toBe(true);
  const after = await f.owner.query(api.sessions.get, f.scope);
  expect(after.media.kind).toBe("ready");
  expect(after.cleanupPending).toBe(false);
  expect(after.authorizationVersion).toBe(2);
});

it.each(["completion", "failure"])("late provisioning %s after cleanup restores the current-generation blocker", async result => {
  const f = await setup();
  await f.owner.mutation(api.mediaState.recover, f.scope);
  const cleanup = await f.t.run(ctx => ctx.db.query("mediaCleanup").first());
  if (!cleanup) throw new Error("Missing cleanup");
  const claim = await f.t.mutation(internal.media.claimCleanup, { cleanupId: cleanup._id });
  if (!claim) throw new Error("Missing cleanup claim");
  await f.t.mutation(internal.media.finishCleanup, { cleanupId: cleanup._id, attempt: claim.attempt, roomId: roomName });
  expect((await f.owner.query(api.sessions.get, f.scope)).cleanupPending).toBe(false);
  if (result === "completion") {
    expect(await f.t.mutation(internal.mediaState.finishProvision, { roomKey: f.room._id, roomSid })).toBe(false);
  } else await f.t.mutation(internal.mediaState.failProvision, { roomKey: f.room._id });
  expect((await f.owner.query(api.sessions.get, f.scope)).cleanupPending).toBe(true);
  await expect(f.owner.mutation(internal.mediaState.beginProvision, { ...f.scope,
    roomName: "reflex-00000000-0000-4000-8000-000000000002" })).rejects.toThrow("MEDIA_CLEANUP_REQUIRED");
});

it.each(["unknown-participant", "missing-owner"])("increments past a just-confirmed connection before invalidating: %s", async scenario => {
  const f = await setup();
  const helper = await f.reserve();
  const participants = scenario === "unknown-participant"
    ? [helper, f.ownerMember, { identity: "unissued", sid: "PI_unissued", state: "active" as const }]
    : [helper];
  await f.snapshot(participants);
  const after = await f.owner.query(api.sessions.get, f.scope);
  expect(after.authorizationVersion).toBe(3);
  expect(after.slot.kind).toBe("empty");
  expect(after.media.kind).toBe("retired");
  expect(after.cleanupPending).toBe(true);
});

it("a stale leave event reconciles membership instead of clearing a current connection", async () => {
  const f = await setup();
  const helper = await f.reserve();
  await f.snapshot([f.ownerMember, helper]);
  await f.t.mutation(internal.mediaState.receiveEvent, { eventId: "EV_stale_left", digest: "simulated-digest",
    roomId: roomName, roomSid: "RM_old", event: "participant_left" });
  await f.snapshot([f.ownerMember, helper]);
  const current = await f.owner.query(api.sessions.get, f.scope);
  expect(current.slot.kind === "connected" && current.slot.participantSid).toBe(helper.sid);
  expect(current.authorizationVersion).toBe(2);
});

it("a new participant SID cannot take over a previously bound identity", async () => {
  const f = await setup();
  const helper = await f.reserve();
  await f.snapshot([f.ownerMember, helper]);
  await f.snapshot([f.ownerMember, { ...helper, sid: "PI_another_device" }]);
  const current = await f.owner.query(api.sessions.get, f.scope);
  expect(current.slot.kind).toBe("empty");
  expect(current.media.kind).toBe("retired");
});

async function signedBody(body: string) {
  const token = new AccessToken("simulated-key", "simulated-secret-local-only", { ttl: 60 });
  token.sha256 = createHash("sha256").update(body).digest("base64");
  return token.toJwt();
}
function configure() {
  vi.stubEnv("LIVEKIT_URL", "wss://simulated.example");
  vi.stubEnv("LIVEKIT_API_KEY", "simulated-key");
  vi.stubEnv("LIVEKIT_API_SECRET", "simulated-secret-local-only");
}

it("HTTP ingress verifies exact raw-body signatures before durable deduplicated event writes", async () => {
  configure();
  const f = await setup();
  const body = JSON.stringify({ id: "EV_signed", event: "participant_joined", created_at: 1_800_000_000,
    room: { name: roomName, sid: roomSid }, participant: { identity: "unissued", sid: "PI_unissued", metadata: "role=owner" } });
  const authorization = await signedBody(body);
  expect((await f.t.fetch("/livekit/webhook", { method: "POST", body })).status).toBe(401);
  expect((await f.t.fetch("/livekit/webhook", { method: "POST", body: body + " ", headers: { Authorization: authorization } })).status).toBe(401);
  expect(await f.t.run(ctx => ctx.db.query("mediaEvents").collect())).toHaveLength(0);
  for (let i = 0; i < 2; i++) {
    expect((await f.t.fetch("/livekit/webhook", { method: "POST", body,
      headers: { Authorization: authorization, "Content-Type": "application/webhook+json" } })).status).toBe(204);
  }
  expect(await f.t.run(ctx => ctx.db.query("mediaEvents").collect())).toHaveLength(1);
  // A signed event is a wakeup signal, not a grant from participant metadata.
  expect((await f.owner.query(api.sessions.get, f.scope)).slot.kind).toBe("empty");
});

it("HTTP ingress bounds bodies and fails closed when verifier configuration is absent", async () => {
  const t = convexTest(schema, modules);
  expect((await t.fetch("/livekit/webhook", { method: "POST", body: "x".repeat(256 * 1024 + 1), headers: { Authorization: "x" } })).status).toBe(413);
  vi.stubEnv("LIVEKIT_API_SECRET", undefined);
  expect((await t.fetch("/livekit/webhook", { method: "POST", body: "{}", headers: { Authorization: "x" } })).status).toBe(503);
});
