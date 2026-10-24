import type { PaginationOptions, PaginationResult } from "convex/server";
import { ConvexError } from "convex/values";
import type { Id } from "../_generated/dataModel";

const MAX_PAGE_SIZE = 50;
const MAX_CURSOR_LENGTH = 16_384;

type FriendListScope = { userId: Id<"users"> } & (
  | { view: "owned" | "helping"; state: "accepted" | "pending" }
  | { view: "pendingInvites" | "joinableSessions" }
);

function scopeKey(scope: FriendListScope): string {
  return JSON.stringify("state" in scope
    ? [1, scope.userId, scope.view, scope.state]
    : [1, scope.userId, scope.view]);
}

function unwrapCursor(cursor: string | null, scope: FriendListScope): string | null {
  if (cursor === null) return null;
  if (cursor.length > MAX_CURSOR_LENGTH) throw new ConvexError("INVALID_CURSOR");
  let envelope: unknown;
  try { envelope = JSON.parse(cursor); }
  catch { throw new ConvexError("INVALID_CURSOR"); }
  if (typeof envelope !== "object" || envelope === null || Array.isArray(envelope) ||
      Object.keys(envelope).length !== 2 ||
      !("scope" in envelope) || envelope.scope !== scopeKey(scope) ||
      !("cursor" in envelope) || typeof envelope.cursor !== "string") {
    throw new ConvexError("INVALID_CURSOR");
  }
  return envelope.cursor;
}

/** Cursor scope prevents accidental cross-account/view reuse. It is not authentication:
 * every query must still constrain its index by the authenticated account. */
export function friendPageOptions(options: PaginationOptions, scope: FriendListScope): PaginationOptions {
  if (!Number.isSafeInteger(options.numItems) || options.numItems < 1 || options.numItems > MAX_PAGE_SIZE) {
    throw new ConvexError("INVALID_PAGE_SIZE");
  }
  return {
    ...options,
    cursor: unwrapCursor(options.cursor, scope),
    ...(options.endCursor === undefined ? {} : { endCursor: unwrapCursor(options.endCursor, scope) }),
    // Server-owned scan budgets also apply to reactive endCursor ranges.
    maximumRowsRead: MAX_PAGE_SIZE,
    maximumBytesRead: 65_536,
  };
}

export function friendPageResult<T>(result: PaginationResult<T>, scope: FriendListScope): PaginationResult<T> {
  const wrap = (cursor: string) => JSON.stringify({ scope: scopeKey(scope), cursor });
  return {
    ...result,
    continueCursor: wrap(result.continueCursor),
    ...(result.splitCursor === undefined ? {} : {
      splitCursor: result.splitCursor === null ? null : wrap(result.splitCursor),
    }),
  };
}
