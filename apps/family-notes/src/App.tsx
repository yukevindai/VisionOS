import { useAuth0 } from "@auth0/auth0-react";
import { useAction, useConvexAuth, useMutation, useQuery } from "convex/react";
import { useEffect, useRef, useState } from "react";
import { api, type NoteKind, type OwnerNote } from "./api";
import { Listening, MyVoice } from "./voice";

const PAGE = { numItems: 20, cursor: null };
const INVITE_LIFETIME_MS = 24 * 60 * 60 * 1000;
const time = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const errorText = (e: unknown) => {
  const code = (e as { data?: { code?: string } })?.data?.code;
  const known: Record<string, string> = {
    INVALID_NOTE: "Write 1 to 220 characters.", INVALID_PLACE: "Keep the place under 40 characters.",
    NOTE_RATE_LIMITED: "You've sent several notes in a row. Wait a few minutes.",
  };
  return (code && known[code]) || (e instanceof Error ? e.message.replace(/^.*Uncaught Error: /, "").split("\n")[0] : "Something went wrong.");
};

export function App() {
  const { isLoading: authLoading, isAuthenticated: auth0User, loginWithRedirect, logout, user } = useAuth0();
  const { isAuthenticated, isLoading } = useConvexAuth();
  const ensure = useMutation(api.ensure);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<"helper" | "owner">(() => new URLSearchParams(location.search).get("view") === "owner" ? "owner" : "helper");

  useEffect(() => {
    if (isAuthenticated) ensure({}).then(() => setReady(true)).catch(() => setReady(false));
  }, [isAuthenticated, ensure]);

  if (authLoading || isLoading) return <main className="shell"><p>Loading…</p></main>;
  if (!auth0User) {
    return <main className="shell">
      <p className="eyebrow">VisionOS</p><h1>Family notes</h1>
      <p>Leave a note once. They hear it when they need it, and you see when they heard it.</p>
      <button onClick={() => loginWithRedirect({ appState: { returnTo: location.pathname + location.search } })}>Sign in</button>
    </main>;
  }
  return <main className="shell">
    <header>
      <div><p className="eyebrow">VisionOS</p><h1>Family notes</h1></div>
      <button className="secondary" onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>Sign out</button>
    </header>
    <p className="account">Signed in as {user?.name ?? user?.email}</p>
    <div className="tabs" role="group" aria-label="View">
      <button aria-pressed={tab === "helper"} onClick={() => setTab("helper")}>I'm helping someone</button>
      <button aria-pressed={tab === "owner"} onClick={() => setTab("owner")}>Notes for me</button>
    </div>
    {!ready ? <p>Setting up your account…</p> : tab === "helper" ? <HelperView /> : <OwnerView />}
  </main>;
}

// ---------- Helper ----------

function HelperView() {
  const people = useQuery(api.helping, { direction: "helping", state: "accepted", paginationOpts: PAGE });
  const [ownerId, setOwnerId] = useState("");
  const rows = people?.page.filter((r) => r.counterpart) ?? [];
  useEffect(() => { if (!ownerId && rows[0]?.counterpart) setOwnerId(rows[0].counterpart.id); }, [rows, ownerId]);

  if (people === undefined) return <p>Loading the people you help…</p>;
  return <>
    {rows.length > 0 && <section className="card">
      <label htmlFor="person">Note for</label>
      <select id="person" value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
        {rows.map((r) => <option key={r.counterpart!.id} value={r.counterpart!.id}>{r.counterpart!.displayName}</option>)}
      </select>
      {ownerId && <Composer ownerId={ownerId} />}
    </section>}
    {rows.length > 0 && <MyVoice />}
    {ownerId && rows.length > 0 && <SentNotes ownerId={ownerId} />}
    <RedeemInvite first={rows.length === 0} />
  </>;
}

function RedeemInvite({ first }: { first: boolean }) {
  const redeem = useAction(api.redeemInvite);
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("");
  const submit = async () => {
    setStatus("Checking…");
    try {
      const result = await redeem({ code: code.trim() });
      setCode("");
      setStatus(result.state === "accepted" ? "You're connected." : "Sent. They need to approve you in VisionOS.");
    } catch (e) { setStatus(errorText(e)); }
  };
  return <section className="card">
    <h2>{first ? "Join someone's VisionOS" : "Add another person"}</h2>
    <p>Ask them for an invite code. They approve you before you can send notes.</p>
    <div className="row">
      <input aria-label="Invite code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Invite code"
        style={{ flex: 1, minHeight: 44, borderRadius: 10, border: "1px solid #b9cbc6", padding: ".6rem" }} />
      <button onClick={submit} disabled={!code.trim()}>Join</button>
    </div>
    <p className="status" aria-live="polite">{status}</p>
  </section>;
}

function Composer({ ownerId }: { ownerId: string }) {
  const create = useMutation(api.create);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<NoteKind>("info");
  const [place, setPlace] = useState("");
  const [reviewDays, setReviewDays] = useState(30);
  const [status, setStatus] = useState("");
  const send = async () => {
    setStatus("Sending…");
    try {
      await create({ ownerId, text, kind, reviewDays, ...(place.trim() ? { place } : {}) });
      setText(""); setStatus("Sent. They'll hear it with your name first.");
    } catch (e) { setStatus(errorText(e)); }
  };
  return <div style={{ marginTop: "1rem" }}>
    <label htmlFor="text">Note ({text.trim().length}/220)</label>
    <textarea id="text" value={text} maxLength={220} onChange={(e) => setText(e.target.value)}
      placeholder="The white bottle is your new blood pressure medicine." />
    <div className="row" style={{ marginTop: ".6rem" }}>
      <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value as NoteKind)} style={{ width: "auto" }}>
        <option value="info">Note</option><option value="warning">Safety warning</option>
      </select>
      <input aria-label="Place (optional)" placeholder="Place, e.g. kitchen" value={place} maxLength={40}
        onChange={(e) => setPlace(e.target.value)} style={{ flex: 1, minHeight: 44, borderRadius: 10, border: "1px solid #b9cbc6", padding: ".6rem" }} />
      <select aria-label="Check again in" value={reviewDays} onChange={(e) => setReviewDays(Number(e.target.value))} style={{ width: "auto" }}>
        <option value={7}>Check again in 1 week</option><option value={30}>Check again in 30 days</option>
        <option value={90}>Check again in 90 days</option><option value={365}>Check again in a year</option>
      </select>
      <button onClick={send} disabled={!text.trim()}>Send</button>
    </div>
    <p className="status" aria-live="polite">{status}</p>
    <p className="status">Place helps match the note to the right object: reassurance ("this is candy") is only read in the same place, warnings anywhere. After the check date the note is read as possibly out of date until you confirm it.</p>
    <p className="status">Notes are your words, read aloud with your name. VisionOS never treats them as permission to pay or as proof something is safe.</p>
  </div>;
}

function SentNotes({ ownerId }: { ownerId: string }) {
  const notes = useQuery(api.byAuthor, { ownerId });
  const remove = useMutation(api.remove);
  const confirm = useMutation(api.confirmStillCorrect);
  if (!notes?.length) return null;
  const review = notes.filter((n) => n.needsReview).length;
  return <section className="card"><h2>Your notes</h2>
    {review > 0 && <p className="warning" aria-live="polite">{review} note{review > 1 ? "s" : ""} need{review > 1 ? "" : "s"} review</p>}
    {notes.map((n) => <div className="note" key={n.noteId}>
      <div>{n.kind === "warning" && <span className="warning">Safety · </span>}{n.text}</div>
      <div className="meta">
        {n.place ? `${n.place} · ` : ""}{time(n.createdAt)} ·{" "}
        <strong>{n.heardAt ? `Heard at ${time(n.heardAt)}` : "Not heard yet"}</strong> ·{" "}
        {n.audioStatus === "ready" ? (n.audioVoice === "helper" ? "Ready in your voice" : "Voice ready")
          : n.audioStatus === "pending" ? "Preparing voice…" : "Will be read by the device voice"}
        {n.needsReview && <> · <span className="warning">Needs review</span>{" "}
          <button onClick={() => confirm({ noteId: n.noteId })}>Still correct</button></>}
        {" "}<button className="secondary" onClick={() => remove({ noteId: n.noteId })}>Delete</button>
      </div>
    </div>)}
  </section>;
}

// ---------- Owner (the VisionOS user) ----------

/** Plays one note to the end. Resolves true only if it was actually spoken in full. */
function playNote(n: OwnerNote): Promise<boolean> {
  const speakLocally = () => new Promise<boolean>((resolve) => {
    if (!("speechSynthesis" in window)) return resolve(false);
    const u = new SpeechSynthesisUtterance(n.spoken); // same words, device voice
    u.onend = () => resolve(true); u.onerror = () => resolve(false);
    window.speechSynthesis.speak(u);
  });
  window.speechSynthesis?.cancel();
  if (!n.audioUrl) return speakLocally();
  return new Promise<boolean>((resolve) => {
    const audio = new Audio(n.audioUrl!);
    audio.onended = () => resolve(true);
    audio.onerror = () => speakLocally().then(resolve);
    audio.play().catch(() => speakLocally().then(resolve));
  });
}

function OwnerView() {
  const notes = useQuery(api.forOwner, {});
  const [search, setSearch] = useState("");
  const hits = useQuery(api.search, search.trim() ? { query: search } : "skip");
  const markHeard = useMutation(api.markHeard);
  const remove = useMutation(api.remove);
  const [autoRead, setAutoRead] = useState(false);
  const [speaking, setSpeaking] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const played = useRef(new Set<string>());
  const busy = useRef(false);

  const play = async (n: OwnerNote) => {
    busy.current = true; setSpeaking(n.noteId); played.current.add(n.noteId);
    try {
      // "Heard" is recorded only after the whole note was played, so helpers never get a false receipt.
      if (await playNote(n)) await markHeard({ noteId: n.noteId });
    } finally { busy.current = false; setSpeaking(null); }
  };

  useEffect(() => { // auto-read: speak new, unheard notes one at a time as they arrive live
    if (!autoRead || !notes || busy.current) return;
    const next = [...notes].reverse().find((n) => !n.heardAt && !played.current.has(n.noteId) && n.audioStatus !== "pending");
    if (next) { setAnnounce(`Note from ${next.authorName}`); void play(next); }
  }, [autoRead, notes, speaking]);

  if (notes === undefined) return <p>Loading your notes…</p>;
  const unheard = notes.filter((n) => !n.heardAt);
  return <>
    <p className="status" aria-live="assertive">{announce}</p>
    <section className="card">
      <h2>{unheard.length ? `${unheard.length} new note${unheard.length > 1 ? "s" : ""}` : "No new notes"}</h2>
      <div className="row">
        <button aria-pressed={autoRead} onClick={() => setAutoRead((on) => !on)}>
          {autoRead ? "Reading new notes aloud: on" : "Read new notes aloud automatically"}
        </button>
        {!autoRead && unheard[0] && <button className="secondary" onClick={() => play(unheard[0]!)}>Play newest</button>}
      </div>
      {autoRead && <p className="status">New notes are spoken as soon as they arrive.</p>}
      <label htmlFor="search" style={{ marginTop: "1rem" }}>Search notes</label>
      <input id="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="medicine, kitchen…"
        style={{ width: "100%", minHeight: 44, borderRadius: 10, border: "1px solid #b9cbc6", padding: ".6rem" }} />
    </section>
    <section className="card"><h2>{search.trim() ? "Search results" : "All notes"}</h2>
      {(search.trim() ? hits ?? [] : notes).map((n) => <div className="note" key={n.noteId}>
        <div>{n.kind === "warning" && <span className="warning">Safety · </span>}<strong>{n.authorName}:</strong> {n.text}</div>
        <div className="meta row">
          <span>{n.place ? `${n.place} · ` : ""}{time(n.createdAt)}{n.heardAt ? ` · heard ${time(n.heardAt)}` : " · new"}{n.stale ? " · may be out of date" : ""}
            {n.audioVoice === "helper" ? ` · in ${n.authorName}'s own voice (AI copy, with consent)` : ""}</span>
          <button onClick={() => play(n)} disabled={speaking !== null}>{speaking === n.noteId ? "Playing…" : "Play"}</button>
          <button className="secondary" onClick={() => remove({ noteId: n.noteId })}>Delete</button>
        </div>
      </div>)}
    </section>
    <Listening />
    <Devices />
    <Helpers />
  </>;
}

function Devices() {
  const devices = useQuery(api.devices, {});
  const pair = useAction(api.pairDevice);
  const revoke = useMutation(api.revokeDevice);
  const [label, setLabel] = useState("Demo laptop");
  const [key, setKey] = useState("");
  const [status, setStatus] = useState("");
  const create = async () => {
    setStatus("Pairing…");
    try { setKey((await pair({ label })).key); setStatus("Copy this key into backend/.env on the laptop. It is shown only once."); }
    catch (e) { setStatus(errorText(e)); }
  };
  return <section className="card"><h2>Devices that read your notes</h2>
    <p className="status">A paired VisionOS laptop speaks new notes between tasks, never during guidance.</p>
    {(devices ?? []).map((d) => <div className="note row" key={d.deviceId}>
      <span style={{ flex: 1 }}>{d.label} · paired {time(d.createdAt)}</span>
      <button className="secondary" onClick={() => revoke({ deviceId: d.deviceId })}>Remove</button>
    </div>)}
    <div className="row" style={{ marginTop: ".6rem" }}>
      <input aria-label="Device name" value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)}
        style={{ flex: 1, minHeight: 44, borderRadius: 10, border: "1px solid #b9cbc6", padding: ".6rem" }} />
      <button onClick={create} disabled={!label.trim()}>Pair this laptop</button>
    </div>
    {key && <p><code style={{ wordBreak: "break-all" }}>FAMILY_NOTES_DEVICE_KEY={key}</code>{" "}
      <button className="secondary" onClick={() => navigator.clipboard?.writeText(`FAMILY_NOTES_DEVICE_KEY=${key}`)}>Copy</button></p>}
    <p className="status" aria-live="polite">{status}</p>
  </section>;
}

function Helpers() {
  const helpers = useQuery(api.owned, { direction: "owned", state: "accepted", paginationOpts: PAGE });
  const pending = useQuery(api.pendingInvites, { paginationOpts: PAGE });
  const createInvite = useAction(api.createInvite);
  const approve = useMutation(api.approveInvite);
  const revoke = useMutation(api.revoke);
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("");
  const invite = async () => {
    setStatus("Creating…");
    try { const r = await createInvite({ lifetimeMs: INVITE_LIFETIME_MS }); setCode(r.code); setStatus("Share this code. It works once, for 24 hours."); }
    catch (e) { setStatus(errorText(e)); }
  };
  return <section className="card"><h2>People who can leave you notes</h2>
    <p className="status">These are your VisionOS helpers. The same people can join video help.</p>
    {(helpers?.page ?? []).filter((r) => r.counterpart).map((r) => <div className="note row" key={r.counterpart!.id}>
      <span style={{ flex: 1 }}>{r.counterpart!.displayName}</span>
      <button className="secondary" onClick={() => {
        if (confirm(`Remove ${r.counterpart!.displayName}? This ends all their VisionOS access, including video help, not only notes.`)) void revoke({ helperId: r.counterpart!.id });
      }}>Remove access</button>
    </div>)}
    {(pending?.page ?? []).map((p) => <div className="note row" key={p.inviteId}>
      <span style={{ flex: 1 }}>{p.requester?.displayName ?? "Someone"} wants to help you</span>
      <button onClick={() => approve({ inviteId: p.inviteId })}>Approve</button>
    </div>)}
    <div className="row" style={{ marginTop: ".6rem" }}>
      <button onClick={invite}>Invite a helper</button>
      {code && <code style={{ wordBreak: "break-all", fontSize: "1.05rem" }}>{code}</code>}
      {code && <button className="secondary" onClick={() => navigator.clipboard?.writeText(code)}>Copy</button>}
    </div>
    <p className="status" aria-live="polite">{status}</p>
  </section>;
}
