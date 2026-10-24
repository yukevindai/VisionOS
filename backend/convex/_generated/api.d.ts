/* eslint-disable */
/**
 * Generated `api` utility.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import type {
  ApiFromModules,
  FilterApi,
  FunctionReference,
} from "convex/server";
import type * as assistance from "../assistance.js";
import type * as authTables from "../authTables.js";
import type * as crons from "../crons.js";
import type * as friends from "../friends.js";
import type * as http from "../http.js";
import type * as inviteLimitTables from "../inviteLimitTables.js";
import type * as inviteLimits from "../inviteLimits.js";
import type * as invites from "../invites.js";
import type * as lib_auth from "../lib/auth.js";
import type * as lib_cartesia from "../lib/cartesia.js";
import type * as lib_friendPagination from "../lib/friendPagination.js";
import type * as lib_leases from "../lib/leases.js";
import type * as lib_livekitProvider from "../lib/livekitProvider.js";
import type * as lib_mediaLifecycle from "../lib/mediaLifecycle.js";
import type * as lib_visionService from "../lib/visionService.js";
import type * as livekit from "../livekit.js";
import type * as media from "../media.js";
import type * as mediaRequestLimitTables from "../mediaRequestLimitTables.js";
import type * as mediaRequestLimits from "../mediaRequestLimits.js";
import type * as mediaState from "../mediaState.js";
import type * as mediaTables from "../mediaTables.js";
import type * as sessionTables from "../sessionTables.js";
import type * as sessions from "../sessions.js";
import type * as speech from "../speech.js";
import type * as speechCache from "../speechCache.js";
import type * as speechTables from "../speechTables.js";
import type * as users from "../users.js";
import type * as vision from "../vision.js";

/**
 * A utility for referencing Convex functions in your app's API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = api.myModule.myFunction;
 * ```
 */
declare const fullApi: ApiFromModules<{
  assistance: typeof assistance;
  authTables: typeof authTables;
  crons: typeof crons;
  friends: typeof friends;
  http: typeof http;
  inviteLimitTables: typeof inviteLimitTables;
  inviteLimits: typeof inviteLimits;
  invites: typeof invites;
  "lib/auth": typeof lib_auth;
  "lib/cartesia": typeof lib_cartesia;
  "lib/friendPagination": typeof lib_friendPagination;
  "lib/leases": typeof lib_leases;
  "lib/livekitProvider": typeof lib_livekitProvider;
  "lib/mediaLifecycle": typeof lib_mediaLifecycle;
  "lib/visionService": typeof lib_visionService;
  livekit: typeof livekit;
  media: typeof media;
  mediaRequestLimitTables: typeof mediaRequestLimitTables;
  mediaRequestLimits: typeof mediaRequestLimits;
  mediaState: typeof mediaState;
  mediaTables: typeof mediaTables;
  sessionTables: typeof sessionTables;
  sessions: typeof sessions;
  speech: typeof speech;
  speechCache: typeof speechCache;
  speechTables: typeof speechTables;
  users: typeof users;
  vision: typeof vision;
}>;
export declare const api: FilterApi<
  typeof fullApi,
  FunctionReference<any, "public">
>;
export declare const internal: FilterApi<
  typeof fullApi,
  FunctionReference<any, "internal">
>;
