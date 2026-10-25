import { componentsGeneric } from "convex/server";
import { HOUR, RateLimiter } from "@convex-dev/rate-limiter";
import { ActionRetrier } from "@convex-dev/action-retrier";
import type { ComponentApi as RateLimiterApi } from "@convex-dev/rate-limiter/_generated/component.js";
import type { ComponentApi as RetrierApi } from "@convex-dev/action-retrier/_generated/component.js";

// Components installed in convex.config.ts. Typed here so offline codegen (no deployment)
// does not need to emit `components`; a deployed `convex dev` resolves the same names.
const components = componentsGeneric() as unknown as { rateLimiter: RateLimiterApi; actionRetrier: RetrierApi };

/** Per-helper note budget: a burst of 5, refilling to 20 per hour. Transactional with the insert. */
export const NOTE_RATE = { kind: "token bucket", rate: 20, period: HOUR, capacity: 5 } as const;
export const noteLimiter = new RateLimiter(components.rateLimiter, { familyNote: NOTE_RATE });

/** Cartesia rendering: first try now, then up to 2 retries with exponential backoff (2 s base) and jitter. */
export const NOTE_RENDER_RETRY = { initialBackoffMs: 2_000, base: 2, maxFailures: 2 } as const;
export const noteRetrier = new ActionRetrier(components.actionRetrier, NOTE_RENDER_RETRY);
