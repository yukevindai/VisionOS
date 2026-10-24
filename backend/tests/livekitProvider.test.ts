import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccessToken, TokenVerifier } from "livekit-server-sdk";
import {
  createLiveKitProvider,
  readLiveKitConfig,
  signJoinToken,
  verifyLiveKitWebhook,
  type LiveKitConfig,
} from "../convex/lib/livekitProvider";

const config: LiveKitConfig = {
  serverUrl: "wss://example.livekit.cloud",
  apiUrl: "https://example.livekit.cloud",
  apiKey: "test-key",
  apiSecret: "test-secret-that-is-only-local",
};

afterEach(() => vi.unstubAllGlobals());

function decode(token: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(token.split(".")[1] ?? "", "base64url").toString());
}

describe("LiveKit configuration and tokens", () => {
  it("requires secure configured origins and all credentials", () => {
    vi.stubEnv("LIVEKIT_URL", "wss://example.livekit.cloud");
    vi.stubEnv("LIVEKIT_API_KEY", "test-key");
    vi.stubEnv("LIVEKIT_API_SECRET", "test-secret-that-is-only-local");
    expect(readLiveKitConfig()).toEqual(config);
    vi.stubEnv("LIVEKIT_URL", "ws://example.livekit.cloud");
    expect(() => readLiveKitConfig()).toThrow("secure LiveKit origin");
    vi.stubEnv("LIVEKIT_URL", "https://example.livekit.cloud/path");
    expect(() => readLiveKitConfig()).toThrow("secure LiveKit origin");
    vi.stubEnv("LIVEKIT_URL", "wss://example.livekit.cloud");
    vi.stubEnv("LIVEKIT_API_SECRET", "");
    expect(() => readLiveKitConfig()).toThrow("LIVEKIT_API_SECRET must be configured");
    vi.unstubAllEnvs();
  });

  it.each(["owner", "helper", "worker"] as const)(
    "signs a bounded %s grant with no admin authority",
    async (role) => {
      const deadline = Date.now() + (role === "helper" ? 15_000 : 120_000);
      const jwt = await signJoinToken(
        { roomName: "room_1", identity: `${role}:1`, role, expiresAtMs: deadline },
        config,
      );
      await expect(new TokenVerifier(config.apiKey, config.apiSecret).verify(jwt)).resolves.toBeDefined();
      const claims = decode(jwt);
      const grant = claims.video;
      expect(grant).toMatchObject({
        room: "room_1",
        roomJoin: true,
        canPublish: role !== "worker",
        canSubscribe: true,
        canPublishData: role === "worker",
        canUpdateOwnMetadata: false,
        roomAdmin: false,
        roomCreate: false,
        roomList: false,
        roomRecord: false,
      });
      expect(grant).toMatchObject({
        canPublishSources: role === "worker" ? [] : ["camera", "microphone"],
      });
      expect(claims.exp).toBeTypeOf("number");
      expect(Number(claims.exp)).toBeLessThanOrEqual(Math.floor(deadline / 1_000));
      expect(Number(claims.exp) - Number(claims.nbf)).toBeLessThanOrEqual(120);
      expect(claims.sub).toBe(`${role}:1`);
    },
  );

  it("rejects expired and invalid token inputs", async () => {
    await expect(
      signJoinToken(
        { roomName: "room_1", identity: "helper:1", role: "helper", expiresAtMs: Date.now() },
        config,
      ),
    ).rejects.toThrow("deadline");
    await expect(
      signJoinToken(
        { roomName: "invalid room", identity: "helper:1", role: "helper", expiresAtMs: Date.now() + 15_000 },
        config,
      ),
    ).rejects.toThrow("room name");
  });
});

describe("LiveKit RoomServiceClient wrapper", () => {
  it("makes bounded real SDK RPCs and maps membership", async () => {
    const requests: Array<{ method: string; body: Record<string, unknown>; timeout: number }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: URL, init: RequestInit) => {
      const method = url.pathname.split("/").at(-1) ?? "";
      requests.push({
        method,
        body: JSON.parse(String(init.body)),
        timeout: init.signal ? 5 : 0,
      });
      const response =
        method === "CreateRoom" ? { name: "room_1", sid: "RM_1" } :
        method === "ListRooms" ? { rooms: [{ name: "room_1", sid: "RM_1" }] } :
        method === "ListParticipants" ? { participants: [
          { identity: "helper:1", sid: "PA_1", state: "ACTIVE", metadata: "untrusted" },
          { identity: "owner:1", sid: "PA_2", state: "JOINING" },
        ] } : {};
      return new Response(JSON.stringify(response), { status: 200 });
    }));
    const provider = createLiveKitProvider(config);
    await expect(provider.createRoom({ name: "room_1" })).resolves.toEqual({ name: "room_1", sid: "RM_1" });
    await expect(provider.getRoom("room_1")).resolves.toEqual({ name: "room_1", sid: "RM_1" });
    await expect(provider.listParticipants("room_1")).resolves.toEqual([
      { identity: "helper:1", sid: "PA_1", state: "active" },
      { identity: "owner:1", sid: "PA_2", state: "other" },
    ]);
    await provider.removeParticipant({ roomName: "room_1", identity: "helper:1", revokeBeforeSeconds: 1_800_000_000 });
    await provider.deleteRoom("room_1");
    expect(requests.map((request) => request.method)).toEqual([
      "CreateRoom", "ListRooms", "ListParticipants", "RemoveParticipant", "DeleteRoom",
    ]);
    expect(requests[0]?.body).toMatchObject({ maxParticipants: 3, name: "room_1" });
    expect(requests[3]?.body).toMatchObject({ room: "room_1", identity: "helper:1", revokeTokenTs: "1800000000" });
    expect(requests.every((request) => request.timeout === 5)).toBe(true);
  });

  it("returns null only for a genuinely absent room", async () => {
    const provider = createLiveKitProvider(config);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ rooms: [] }), { status: 200 })));
    await expect(provider.getRoom("room_1")).resolves.toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ code: "permission_denied", msg: "denied" }),
      { status: 403, statusText: "Forbidden" },
    )));
    await expect(provider.getRoom("room_1")).rejects.toMatchObject({ status: 403 });
  });

  it("makes cleanup idempotent only for an SDK not_found response", async () => {
    const provider = createLiveKitProvider(config);
    const missing = () => new Response(JSON.stringify({ code: "not_found", msg: "absent" }), {
      status: 404,
      statusText: "Not Found",
      headers: { "content-type": "application/json" },
    });
    vi.stubGlobal("fetch", vi.fn(async () => missing()));
    await expect(provider.removeParticipant({
      roomName: "room_1", identity: "helper:1", revokeBeforeSeconds: 1_800_000_000,
    })).resolves.toBeUndefined();
    await expect(provider.deleteRoom("room_1")).resolves.toBeUndefined();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ code: "permission_denied", msg: "denied" }),
      { status: 403, headers: { "content-type": "application/json" } },
    )));
    await expect(provider.removeParticipant({
      roomName: "room_1", identity: "helper:1", revokeBeforeSeconds: 1_800_000_000,
    })).rejects.toMatchObject({ status: 403, code: "permission_denied" });
    await expect(provider.deleteRoom("room_1")).rejects.toMatchObject({ status: 403 });
  });
});

describe("verified LiveKit webhook", () => {
  async function signedWebhook(body: string): Promise<string> {
    const signer = new AccessToken(config.apiKey, config.apiSecret, { ttl: 30 });
    signer.sha256 = createHash("sha256").update(body).digest("base64");
    return signer.toJwt();
  }

  it("verifies a genuine SDK signature and raw body hash, then drops metadata", async () => {
    const body = JSON.stringify({
      id: "EV_1", event: "participant_joined", created_at: 1_800_000_000,
      room: { name: "room_1", sid: "RM_1" },
      participant: { identity: "helper:1", sid: "PA_1", metadata: "untrusted" },
    });
    const authorization = await signedWebhook(body);
    await expect(verifyLiveKitWebhook({ body, authorization }, config)).resolves.toEqual({
      id: "EV_1", event: "participant_joined", createdAt: 1_800_000_000,
      room: { name: "room_1", sid: "RM_1" },
      participant: { identity: "helper:1", sid: "PA_1" },
    });
    await expect(verifyLiveKitWebhook({ body: `${body} `, authorization }, config)).rejects.toThrow();
    await expect(verifyLiveKitWebhook({ body, authorization: "bogus" }, config)).rejects.toThrow();
  });

  it("rejects malformed signed identities and oversized bodies", async () => {
    const body = JSON.stringify({ id: "EV_1", event: "participant_joined", created_at: 1_800_000_000,
      room: { name: "room 1", sid: "RM_1" } });
    const authorization = await signedWebhook(body);
    await expect(verifyLiveKitWebhook({ body, authorization }, config)).rejects.toThrow("room name");
    await expect(verifyLiveKitWebhook({ body: "x".repeat(256 * 1024 + 1), authorization }, config)).rejects.toThrow("webhook request");
  });
});
