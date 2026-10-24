import { makeFunctionReference } from "convex/server";

export type SessionId = string;
export type JoinableSession = { sessionId: SessionId; epoch: string; ownerDisplayName: string };
export type Reservation = {
  kind: "reserved";
  clientInstanceId: string;
  connectionId: string;
  leaseVersion: number;
  expiresAt: number;
};
export type JoinToken = {
  token: string;
  serverUrl: string;
  roomName: string;
  roomSid: string;
  generation: number;
  identity: string;
  expiresAt: number;
};

export type HelperStatus = {
  state: "closed" | "reserved" | "connected";
  nonce: string;
  authorizationVersion?: number;
  validForMs: number;
  roomName?: string;
  roomSid?: string;
  generation?: number;
  participantSid?: string;
  ownerIdentity?: string;
  ownerParticipantSid?: string;
};

type Scope = { sessionId: SessionId; epoch: string };
export type ConnectionScope = Scope & { connectionId: string; leaseVersion: number };
export type HeartbeatScope = ConnectionScope & { authorizationVersion: number };

export const api = {
  ensure: makeFunctionReference<"mutation", Record<string, never>, { _id: string; displayName: string }>("users:ensure"),
  redeemInvite: makeFunctionReference<"action", { code: string }, { state: "pending" | "accepted"; ownerId: string }>("invites:redeemInvite"),
  listJoinable: makeFunctionReference<"query", { paginationOpts: { numItems: number; cursor: string | null } },
    { page: JoinableSession[]; continueCursor: string; isDone: boolean }>("sessions:listJoinable"),
  reserveHelper: makeFunctionReference<"mutation", Scope & { clientInstanceId: string }, Reservation>("assistance:reserveHelper"),
  issueHelperToken: makeFunctionReference<"action", Scope & { clientInstanceId: string }, JoinToken>("livekit:issueHelperToken"),
  helperStatus: makeFunctionReference<"query", ConnectionScope & { nonce: string }, HelperStatus>("assistance:helperStatus"),
  heartbeat: makeFunctionReference<"mutation", HeartbeatScope, { expiresAt: number }>("assistance:heartbeat"),
  leave: makeFunctionReference<"mutation", ConnectionScope, void>("assistance:leave"),
} as const;
