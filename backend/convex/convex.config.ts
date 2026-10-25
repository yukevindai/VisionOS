import { defineApp } from "convex/server";
import rateLimiter from "@convex-dev/rate-limiter/convex.config.js";
import actionRetrier from "@convex-dev/action-retrier/convex.config.js";

const app = defineApp();
app.use(rateLimiter); // per-helper limits on family notes
app.use(actionRetrier); // durable retries for Cartesia note rendering
export default app;
