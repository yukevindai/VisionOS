import { useAuth0 } from "@auth0/auth0-react";
import { useAction, useConvex, useConvexAuth, useMutation, usePaginatedQuery } from "convex/react";
import { DisconnectReason, RemoteTrack, Room, RoomEvent, Track } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ConnectionScope, type HelperStatus, type JoinableSession, type JoinToken } from "./api";

type Call = {
  scope: ConnectionScope;
  owner: string;
  room: Room;
  token: JoinToken;
  localSid: string;
};
type Phase = "idle" | "reserving" | "token" | "connecting" | "verifying" | "live" | "delayed" | "reconnecting";

function describeError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (text.includes("HELPER_SLOT_OCCUPIED")) return "A friend is already assisting. Refresh availability later.";
  if (text.includes("RATE_LIMITED")) return "Media requests are temporarily limited. Wait before choosing Join again.";
  if (text.includes("SHARING_UNAVAILABLE") || text.includes("SESSION_UNAVAILABLE")) return "This session is no longer available.";
  if (text.includes("TOKEN_ALREADY_ISSUED")) return "This room needs owner recovery before another join. Ask the owner to restart sharing.";
  return text;
}

function Status({ children }: { children: React.ReactNode }) {
  return <p className="status" role="status" aria-live="polite">{children}</p>;
}

export function App() {
  const { isLoading, isAuthenticated } = useConvexAuth();
  const { loginWithRedirect, logout, user, error: authError } = useAuth0();
  if (isLoading) return <main className="shell"><h1>VisionOS Friend</h1><Status>Checking sign in…</Status></main>;
  if (!isAuthenticated) return <main className="shell"><h1>VisionOS Friend</h1>
    <p>Sign in to accept a trusted friend invitation and watch an assistance session.</p>
    {authError && <p role="alert">Sign in failed: {authError.message}</p>}
    <button onClick={() => void loginWithRedirect({ appState: { returnTo: window.location.pathname + window.location.search } })}>Sign in</button>
  </main>;
  return <SignedIn name={user?.name || "Friend"} accountId={user?.sub || ""} logout={logout} />;
}

function SignedIn({ name, accountId, logout }: {
  name: string;
  accountId: string;
  logout: ReturnType<typeof useAuth0>["logout"];
}) {
  const convex = useConvex();
  const ensure = useMutation(api.ensure);
  const redeemInvite = useAction(api.redeemInvite);
  const reserveHelper = useMutation(api.reserveHelper);
  const issueHelperToken = useAction(api.issueHelperToken);
  const leave = useMutation(api.leave);
  const heartbeat = useMutation(api.heartbeat);
  const [setup, setSetup] = useState<"pending" | "ready" | "failed">("pending");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [invite, setInvite] = useState(() => new URLSearchParams(window.location.search).get("invite") || "");
  const [phase, setPhase] = useState<Phase>("idle");
  const [call, setCall] = useState<Call | null>(null);
  const [status, setStatus] = useState<HelperStatus | null>(null);
  const statusRef = useRef<HelperStatus | null>(null);
  const callRef = useRef<Call | null>(null);
  const pendingScope = useRef<ConnectionScope | null>(null);
  const joinRevision = useRef(0);
  const roomRef = useRef<Room | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const videoTrackRef = useRef<RemoteTrack | null>(null);
  const candidateTrack = useRef<{ identity: string; sid: string; track: RemoteTrack } | null>(null);
  const lastFrameAt = useRef(0);
  const deadline = useRef(0);
  const accountRef = useRef(accountId);

  useEffect(() => {
    let active = true;
    void ensure({}).then(() => { if (active) setSetup("ready"); })
      .catch(e => { if (active) { setSetup("failed"); setError(describeError(e)); } });
    return () => { active = false; };
  }, [ensure]);

  const close = useCallback((message: string) => {
    joinRevision.current += 1;
    const scope = pendingScope.current;
    pendingScope.current = null;
    deadline.current = 0;
    statusRef.current = null;
    setStatus(null);
    const room = roomRef.current;
    roomRef.current = null;
    videoTrackRef.current?.detach();
    videoTrackRef.current = null;
    candidateTrack.current = null;
    lastFrameAt.current = 0;
    const video = videoRef.current;
    if (video) { video.pause(); video.srcObject = null; }
    // Clear local display and transport before waiting on the network.
    if (room) void room.disconnect();
    callRef.current = null;
    setCall(null);
    setPhase("idle");
    setNotice(message);
    if (scope) void leave(scope).catch(() => { /* lease expires server-side */ });
  }, [leave]);

  useEffect(() => {
    if (accountRef.current !== accountId) {
      accountRef.current = accountId;
      close("Account changed. The call was closed.");
    }
  }, [accountId, close]);

  useEffect(() => {
    const onPageHide = () => close("");
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [close]);

  useEffect(() => () => close(""), [close]);

  useEffect(() => {
    if (!call) return;
    let busy = false;
    const poll = async () => {
      if (busy || callRef.current !== call) return;
      busy = true;
      const nonce = crypto.randomUUID();
      const sentAt = performance.now();
      try {
        const next = await convex.query(api.helperStatus, { ...call.scope, nonce });
        if (callRef.current !== call) return;
        if (next.nonce !== nonce || next.state === "closed" || next.validForMs <= 0) {
          close("Sharing ended or your access changed.");
          return;
        }
        if (next.roomName !== call.token.roomName || next.roomSid !== call.token.roomSid ||
          next.generation !== call.token.generation ||
          (next.state === "connected" && next.participantSid !== call.localSid)) {
          close("The room changed. Join again only after the owner restores sharing.");
          return;
        }
        const newDeadline = sentAt + Math.min(next.validForMs, 15_000) - 750;
        if (performance.now() >= newDeadline) { close("Sharing authorization expired."); return; }
        deadline.current = newDeadline;
        statusRef.current = next;
        setStatus(next);
        if (next.state === "reserved") setPhase("verifying");
        else setPhase(current => current === "verifying" ? "delayed" : current);
      } catch (e) {
        if (callRef.current === call) close(`Connection status unavailable: ${describeError(e)}`);
      } finally { busy = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 4000);
    return () => window.clearInterval(timer);
  }, [call, convex, close]);

  useEffect(() => {
    if (!call || status?.state !== "connected" || !status.ownerIdentity || !status.ownerParticipantSid || !videoRef.current) return;
    const owner = call.room.remoteParticipants.get(status.ownerIdentity);
    if (!owner) return;
    if (owner.sid !== status.ownerParticipantSid) { close("Owner media identity changed."); return; }
    for (const publication of owner.videoTrackPublications.values()) {
      if (publication.source === Track.Source.Camera) publication.setSubscribed(true);
    }
    const candidate = candidateTrack.current;
    if (candidate?.identity !== status.ownerIdentity || candidate.sid !== status.ownerParticipantSid ||
      candidate.track.kind !== Track.Kind.Video || videoTrackRef.current === candidate.track) return;
    videoTrackRef.current?.detach();
    videoTrackRef.current = candidate.track;
    candidate.track.attach(videoRef.current);
    lastFrameAt.current = performance.now();
  }, [call, status, close]);

  useEffect(() => {
    if (!call) return;
    const timer = window.setInterval(() => {
      if (deadline.current && performance.now() >= deadline.current) close("Sharing authorization expired.");
      else if (lastFrameAt.current && performance.now() - lastFrameAt.current > 3000) setPhase("delayed");
    }, 500);
    return () => window.clearInterval(timer);
  }, [call, close]);

  useEffect(() => {
    if (!call || status?.state !== "connected" || !status.authorizationVersion) return;
    let busy = false;
    const send = async () => {
      if (busy || callRef.current !== call) return;
      busy = true;
      try {
        await heartbeat({ ...call.scope, authorizationVersion: status.authorizationVersion! });
      } catch (e) {
        if (callRef.current === call) close(`Connection ended: ${describeError(e)}`);
      } finally { busy = false; }
    };
    void send();
    const timer = window.setInterval(() => void send(), 5000);
    return () => window.clearInterval(timer);
  }, [call, status?.state, status?.authorizationVersion, heartbeat, close]);

  const join = async (item: JoinableSession) => {
    if (phase !== "idle") return;
    setError(""); setNotice(""); setPhase("reserving");
    const revision = ++joinRevision.current;
    const clientInstanceId = crypto.randomUUID().replaceAll("-", "");
    const base = { sessionId: item.sessionId, epoch: item.epoch };
    try {
      const reservation = await reserveHelper({ ...base, clientInstanceId });
      const scope: ConnectionScope = { ...base, connectionId: reservation.connectionId, leaseVersion: reservation.leaseVersion };
      if (revision !== joinRevision.current) { void leave(scope); return; }
      pendingScope.current = scope;
      deadline.current = performance.now() + 12_000;
      setPhase("token");
      const token = await issueHelperToken({ ...base, clientInstanceId });
      if (revision !== joinRevision.current) return;
      if (token.identity !== reservation.connectionId || !token.roomSid || !token.roomName || token.generation < 1) {
        throw new Error("The join token did not match the reservation.");
      }
      setPhase("connecting");
      const room = new Room({ adaptiveStream: true, dynacast: false });
      roomRef.current = room;
      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        const verified = statusRef.current;
        if (verified?.state !== "connected" || participant.identity !== verified.ownerIdentity ||
          participant.sid !== verified.ownerParticipantSid || track.kind !== Track.Kind.Video || publication.source !== Track.Source.Camera) return;
        candidateTrack.current = { identity: participant.identity, sid: participant.sid, track };
        const video = videoRef.current;
        if (video) {
          videoTrackRef.current?.detach();
          videoTrackRef.current = track;
          track.attach(video);
          lastFrameAt.current = performance.now();
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, track => {
        if (roomRef.current !== room) return;
        if (track === videoTrackRef.current) {
          track.detach(); videoTrackRef.current = null; lastFrameAt.current = 0;
          setPhase("delayed");
        }
      });
      room.on(RoomEvent.Reconnecting, () => { if (roomRef.current === room) setPhase("reconnecting"); });
      room.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
        if (roomRef.current === room) close(reason ? `Media disconnected (${reason}).` : "Media disconnected.");
      });
      await room.connect(token.serverUrl, token.token, { autoSubscribe: false });
      if (revision !== joinRevision.current) { void room.disconnect(); return; }
      if (room.name !== token.roomName || await room.getSid() !== token.roomSid || room.localParticipant.identity !== token.identity || !room.localParticipant.sid) {
        throw new Error("Connected media room identity did not match the authorized token.");
      }
      const connected: Call = { scope, owner: item.ownerDisplayName, room, token, localSid: room.localParticipant.sid };
      callRef.current = connected;
      setCall(connected);
      setPhase("verifying");
    } catch (e) {
      if (revision === joinRevision.current) {
        close("");
        setError(describeError(e));
      }
    }
  };

  const signOut = () => {
    close("");
    void logout({ logoutParams: { returnTo: window.location.origin } });
  };

  const redeem = async (event: React.FormEvent) => {
    event.preventDefault(); setError(""); setNotice("");
    try {
      const result = await redeemInvite({ code: invite.trim() });
      setInvite("");
      window.history.replaceState({}, "", window.location.pathname);
      setNotice(result.state === "accepted" ? "Invitation accepted. Your friend’s available sessions will appear below." :
        "Invitation submitted. The owner must approve this account before you can join.");
    } catch (e) { setError(describeError(e)); }
  };

  return <main className="shell">
    <header><div><p className="eyebrow">VisionOS</p><h1>Friend assistance</h1></div>
      <button className="secondary" onClick={signOut}>Sign out</button></header>
    <p className="account">Signed in as {name}</p>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <Status>{notice}</Status>}
    {setup === "failed" ? <p role="alert">Account setup failed. Refresh after checking Auth0 and Convex configuration.</p> :
      setup === "pending" ? <Status>Preparing your account…</Status> : <>
        <section className="card" aria-labelledby="invite-title">
          <h2 id="invite-title">Accept an invitation</h2>
          <p>Ask the person you will help for their private invitation code. An unbound invitation also needs their approval.</p>
          <form onSubmit={event => void redeem(event)}>
            <label htmlFor="invite">Invitation code</label>
            <div className="form-row"><input id="invite" value={invite} onChange={event => setInvite(event.target.value)} autoComplete="off" required />
              <button type="submit">Accept</button></div>
          </form>
        </section>
        {call ? <section className="card call" aria-labelledby="call-title">
          <div className="call-heading"><div><h2 id="call-title">Helping {call.owner}</h2>
            <Status>{phase === "live" ? "Live video" : phase === "delayed" ? "Video delayed or frozen" : phase === "reconnecting" ? "Reconnecting" : "Verifying connection"}</Status></div>
            <button onClick={() => close("You left the session.")}>Leave</button></div>
          <video ref={videoRef} autoPlay playsInline muted aria-label={`${call.owner}'s rear camera`} onStalled={() => setPhase("delayed")}
            onTimeUpdate={event => {
              if (!("requestVideoFrameCallback" in event.currentTarget)) {
                lastFrameAt.current = performance.now(); setPhase("live");
              }
            }}
            onPlaying={event => {
              lastFrameAt.current = performance.now();
              if (!("requestVideoFrameCallback" in event.currentTarget)) setPhase("live");
            }}
            onLoadedData={event => {
              const video = event.currentTarget;
              if ("requestVideoFrameCallback" in video) {
                const frame = () => {
                  if (videoRef.current !== video || !callRef.current) return;
                  lastFrameAt.current = performance.now();
                  setPhase("live");
                  video.requestVideoFrameCallback(frame);
                };
                video.requestVideoFrameCallback(frame);
              }
            }} />
          <p className="hint">Watch only. This browser does not request your camera or microphone.</p>
        </section> : <Availability disabled={phase !== "idle"} phase={phase} onJoin={item => void join(item)} onCancel={() => close("Join canceled.")} />}
      </>}
  </main>;
}

function Availability({ disabled, phase, onJoin, onCancel }: {
  disabled: boolean; phase: Phase; onJoin: (item: JoinableSession) => void; onCancel: () => void;
}) {
  const { results, status, loadMore } = usePaginatedQuery(api.listJoinable, {}, { initialNumItems: 20 });
  return <section className="card" aria-labelledby="sessions-title">
    <h2 id="sessions-title">Available sessions</h2>
    <p>Only people who accepted your friend connection and enabled sharing appear here. Choose Join to watch their live camera.</p>
    {status === "LoadingFirstPage" ? <Status>Checking availability…</Status> :
      results.length === 0 ? <Status>No sessions on this page. Friends may be offline or sharing may be off.</Status> :
        <ul className="sessions">{results.map(item => <li key={`${item.sessionId}:${item.epoch}`}>
          <span>{item.ownerDisplayName}</span><button disabled={disabled} onClick={() => onJoin(item)}>Join</button>
        </li>)}</ul>}
    {status === "CanLoadMore" && <button className="secondary" onClick={() => loadMore(20)} disabled={disabled}>Find more friends</button>}
    {status === "LoadingMore" && <Status>Checking more friends…</Status>}
    {disabled && <div><Status>{phase === "reserving" ? "Reserving the helper slot…" : phase === "token" ? "Authorizing media…" : "Connecting…"}</Status>
      <button className="secondary" onClick={onCancel}>Cancel joining</button></div>}
  </section>;
}
