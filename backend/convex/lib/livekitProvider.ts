"use node";

import {
  AccessToken,
  ParticipantInfo_State,
  RoomServiceClient,
  ServerError,
  TrackSource,
  WebhookReceiver,
} from "livekit-server-sdk";

const MAX_NAME_LENGTH = 128;
const MAX_WEBHOOK_BYTES = 256 * 1024;
const MAX_AUTHORIZATION_LENGTH = 8 * 1024;
const REQUEST_TIMEOUT_SECONDS = 5;

export type LiveKitConfig = Readonly<{
  serverUrl: string;
  apiUrl: string;
  apiKey: string;
  apiSecret: string;
}>;

function required(value: string | undefined, name: string): string {
  if (!value || !value.trim() || value !== value.trim()) {
    throw new Error(`${name} must be configured`);
  }
  return value;
}

function parseLiveKitUrl(value: string): Pick<LiveKitConfig, "serverUrl" | "apiUrl"> {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("LIVEKIT_URL must be a secure LiveKit origin");
  }
  if (
    (url.protocol !== "wss:" && url.protocol !== "https:") ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("LIVEKIT_URL must be a secure LiveKit origin");
  }
  const host = url.host;
  return { serverUrl: `wss://${host}`, apiUrl: `https://${host}` };
}

export function readLiveKitConfig(): LiveKitConfig {
  const urls = parseLiveKitUrl(required(process.env.LIVEKIT_URL, "LIVEKIT_URL"));
  return {
    ...urls,
    apiKey: required(process.env.LIVEKIT_API_KEY, "LIVEKIT_API_KEY"),
    apiSecret: required(process.env.LIVEKIT_API_SECRET, "LIVEKIT_API_SECRET"),
  };
}

function checkedConfig(config: LiveKitConfig | undefined): LiveKitConfig {
  if (!config) return readLiveKitConfig();
  const urls = parseLiveKitUrl(required(config.serverUrl, "LIVEKIT_URL"));
  if (config.apiUrl !== urls.apiUrl) {
    throw new Error("LiveKit API and server origins must match");
  }
  return {
    ...urls,
    apiKey: required(config.apiKey, "LIVEKIT_API_KEY"),
    apiSecret: required(config.apiSecret, "LIVEKIT_API_SECRET"),
  };
}

function checkedName(value: string, label: string): string {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > MAX_NAME_LENGTH ||
    !/^[A-Za-z0-9_.:@-]+$/.test(value)
  ) {
    throw new Error(`Invalid ${label}`);
  }
  return value;
}

function isNotFound(error: unknown): boolean {
  return error instanceof ServerError && error.status === 404 && error.code === "not_found";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export type LiveKitRoom = Readonly<{ name: string; sid: string }>;
export type LiveKitParticipant = Readonly<{
  identity: string;
  sid: string;
  state: "active" | "other";
}>;

export function createLiveKitProvider(config?: LiveKitConfig) {
  const settings = checkedConfig(config);
  const rooms = new RoomServiceClient(
    settings.apiUrl,
    settings.apiKey,
    settings.apiSecret,
    { requestTimeout: REQUEST_TIMEOUT_SECONDS, failover: false },
  );

  return {
    async createRoom({ name }: { name: string }): Promise<LiveKitRoom> {
      const room = await rooms.createRoom({
        name: checkedName(name, "room name"),
        maxParticipants: 3,
        emptyTimeout: 60,
        departureTimeout: 30,
      });
      return { name: checkedName(room.name, "room name"), sid: checkedName(room.sid, "room SID") };
    },

    async getRoom(name: string): Promise<LiveKitRoom | null> {
      const requested = checkedName(name, "room name");
      try {
        const matches = await rooms.listRooms([requested]);
        const room = matches.find((candidate) => candidate.name === requested);
        return room
          ? { name: requested, sid: checkedName(room.sid, "room SID") }
          : null;
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },

    async listParticipants(roomName: string): Promise<LiveKitParticipant[]> {
      const participants = await rooms.listParticipants(checkedName(roomName, "room name"));
      return participants.map((participant) => ({
        identity: checkedName(participant.identity, "participant identity"),
        sid: checkedName(participant.sid, "participant SID"),
        state: participant.state === ParticipantInfo_State.ACTIVE ? "active" : "other",
      }));
    },

    async removeParticipant({
      roomName,
      identity,
      revokeBeforeSeconds,
    }: {
      roomName: string;
      identity: string;
      revokeBeforeSeconds: number;
    }): Promise<void> {
      if (!Number.isSafeInteger(revokeBeforeSeconds) || revokeBeforeSeconds <= 0) {
        throw new Error("Invalid LiveKit revocation cutoff");
      }
      try {
        await rooms.removeParticipant(
          checkedName(roomName, "room name"),
          checkedName(identity, "participant identity"),
          { revokeTokenTs: BigInt(revokeBeforeSeconds) },
        );
      } catch (error) {
        if (!isNotFound(error)) throw error;
      }
    },

    async deleteRoom(roomName: string): Promise<void> {
      try {
        await rooms.deleteRoom(checkedName(roomName, "room name"));
      } catch (error) {
        if (!isNotFound(error)) throw error;
      }
    },
  };
}

export type JoinTokenInput = Readonly<{
  roomName: string;
  identity: string;
  role: "owner" | "helper" | "worker";
  expiresAtMs: number;
}>;

export async function signJoinToken(input: JoinTokenInput, config?: LiveKitConfig): Promise<string> {
  const settings = checkedConfig(config);
  if (input.role !== "owner" && input.role !== "helper" && input.role !== "worker") {
    throw new Error("Invalid LiveKit participant role");
  }
  const room = checkedName(input.roomName, "room name");
  const identity = checkedName(input.identity, "participant identity");
  const now = Date.now();
  if (!Number.isSafeInteger(input.expiresAtMs) || input.expiresAtMs <= now + 1_000) {
    throw new Error("LiveKit token deadline has expired");
  }
  // The SDK signs a relative TTL and rounds the JWT timestamps to seconds.
  // A one-second margin ensures the signed exp cannot exceed the caller's deadline.
  const ttl = Math.min(120, Math.floor((input.expiresAtMs - now) / 1_000) - 1);
  if (ttl < 1) throw new Error("LiveKit token deadline has expired");
  const token = new AccessToken(settings.apiKey, settings.apiSecret, { identity, ttl });
  token.addGrant({
    roomJoin: true,
    room,
    canPublish: input.role !== "worker",
    canPublishSources:
      input.role === "worker" ? [] : [TrackSource.CAMERA, TrackSource.MICROPHONE],
    canSubscribe: true,
    canPublishData: input.role === "worker",
    canUpdateOwnMetadata: false,
    roomCreate: false,
    roomList: false,
    roomAdmin: false,
    roomRecord: false,
    ingressAdmin: false,
  });
  const signed = await token.toJwt();
  // Bound the rounding and any scheduling delay between computing TTL and signing.
  const payload: unknown = JSON.parse(
    Buffer.from(signed.split(".")[1] ?? "", "base64url").toString(),
  );
  if (
    !isRecord(payload) ||
    typeof payload.exp !== "number" ||
    typeof payload.nbf !== "number" ||
    payload.exp > Math.floor(input.expiresAtMs / 1_000) ||
    payload.nbf >= payload.exp ||
    payload.exp - payload.nbf > 120 ||
    payload.exp <= Math.floor(Date.now() / 1_000)
  ) {
    throw new Error("LiveKit token deadline has expired");
  }
  return signed;
}

export type VerifiedLiveKitWebhook = Readonly<{
  id: string;
  event: string;
  createdAt: number;
  room?: LiveKitRoom;
  participant?: Readonly<{ identity: string; sid: string }>;
}>;

export async function verifyLiveKitWebhook(
  input: { body: string; authorization: string | undefined },
  config?: LiveKitConfig,
): Promise<VerifiedLiveKitWebhook> {
  const settings = checkedConfig(config);
  if (
    typeof input.body !== "string" ||
    Buffer.byteLength(input.body, "utf8") < 1 ||
    Buffer.byteLength(input.body, "utf8") > MAX_WEBHOOK_BYTES ||
    typeof input.authorization !== "string" ||
    input.authorization.length < 1 ||
    input.authorization.length > MAX_AUTHORIZATION_LENGTH
  ) {
    throw new Error("Invalid LiveKit webhook request");
  }
  const event = await new WebhookReceiver(settings.apiKey, settings.apiSecret).receive(
    input.body,
    input.authorization,
  );
  const id = checkedName(event.id, "webhook ID");
  const eventName = checkedName(event.event, "webhook event");
  const createdAt = Number(event.createdAt);
  if (!Number.isSafeInteger(createdAt) || createdAt <= 0) {
    throw new Error("Invalid LiveKit webhook timestamp");
  }
  const room = event.room
    ? {
        name: checkedName(event.room.name, "room name"),
        sid: checkedName(event.room.sid, "room SID"),
      }
    : undefined;
  const participant = event.participant
    ? {
        identity: checkedName(event.participant.identity, "participant identity"),
        sid: checkedName(event.participant.sid, "participant SID"),
      }
    : undefined;
  return { id, event: eventName, createdAt, ...(room && { room }), ...(participant && { participant }) };
}
