import { makeFunctionReference } from "convex/server";

export type NoteKind = "info" | "warning";
export type AudioStatus = "pending" | "ready" | "failed";
export type OwnerNote = {
  noteId: string; authorName: string; kind: NoteKind; text: string; place: string | null; spoken: string; stale: boolean;
  createdAt: number; heardAt: number | null; audioStatus: AudioStatus; audioUrl: string | null;
};
export type AuthoredNote = {
  noteId: string; kind: NoteKind; text: string; place: string | null; createdAt: number;
  heardAt: number | null; audioStatus: AudioStatus; needsReview: boolean; reviewAt: number | null;
};
export type Device = { deviceId: string; label: string; createdAt: number };
type Page<T> = { page: T[]; continueCursor: string; isDone: boolean };
export type FriendRow = { friendship: { _id: string }; counterpart: { id: string; displayName: string } | null };

export type PendingInvite = { inviteId: string; expiresAt: number; requester: { id: string; displayName: string } | null };

export const api = {
  ensure: makeFunctionReference<"mutation", Record<string, never>, { _id: string; displayName: string }>("users:ensure"),
  helping: makeFunctionReference<"query", { direction: "helping"; state: "accepted"; paginationOpts: { numItems: number; cursor: string | null } },
    Page<FriendRow>>("friends:list"),
  create: makeFunctionReference<"mutation", { ownerId: string; text: string; kind: NoteKind; place?: string; reviewDays?: number }, string>("notes:create"),
  confirmStillCorrect: makeFunctionReference<"mutation", { noteId: string; reviewDays?: number }, number>("notes:confirmStillCorrect"),
  byAuthor: makeFunctionReference<"query", { ownerId: string }, AuthoredNote[]>("notes:byAuthor"),
  forOwner: makeFunctionReference<"query", Record<string, never>, OwnerNote[]>("notes:forOwner"),
  search: makeFunctionReference<"query", { query: string }, OwnerNote[]>("notes:search"),
  markHeard: makeFunctionReference<"mutation", { noteId: string }, boolean>("notes:markHeard"),
  remove: makeFunctionReference<"mutation", { noteId: string }, boolean>("notes:remove"),
  devices: makeFunctionReference<"query", Record<string, never>, Device[]>("devices:list"),
  pairDevice: makeFunctionReference<"action", { label: string }, { deviceId: string; key: string }>("devicesNode:pair"),
  revokeDevice: makeFunctionReference<"mutation", { deviceId: string }, boolean>("devices:revoke"),
  // Existing friend trust (Kevin's backend): invite, redeem, approve, revoke.
  owned: makeFunctionReference<"query", { direction: "owned"; state: "accepted"; paginationOpts: { numItems: number; cursor: string | null } },
    Page<FriendRow>>("friends:list"),
  createInvite: makeFunctionReference<"action", { lifetimeMs: number }, { inviteId: string; code: string; expiresAt: number }>("invites:createInvite"),
  redeemInvite: makeFunctionReference<"action", { code: string }, { state: "pending" | "accepted"; ownerId: string }>("invites:redeemInvite"),
  pendingInvites: makeFunctionReference<"query", { paginationOpts: { numItems: number; cursor: string | null } }, Page<PendingInvite>>("friends:pendingInvites"),
  approveInvite: makeFunctionReference<"mutation", { inviteId: string }, { helperId: string }>("friends:approveInvite"),
  revoke: makeFunctionReference<"mutation", { helperId: string }, unknown>("friends:revoke"),
} as const;
