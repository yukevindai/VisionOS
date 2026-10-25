import { useAction, useMutation, useQuery } from "convex/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";

// Family Voice, helper side. The helper reads a consent sentence aloud; that recording, made
// here from the microphone (never an uploaded file), becomes their own Cartesia voice, used only
// to read their notes. Owner side: speed, volume and whether helpers' voices are used.

const MIN_S = 10;
const MAX_S = 45;
const RATE = 24_000;

const voiceError = (e: unknown) => {
  const code = (e as { data?: { code?: string } })?.data?.code;
  const known: Record<string, string> = {
    RECORDING_TOO_SHORT: "That was under 10 seconds. Read both paragraphs, then stop.",
    RECORDING_TOO_LONG: "That was too long. Keep it under a minute.",
    INVALID_RECORDING: "The recording could not be read. Try again.",
    VOICE_RATE_LIMITED: "You've tried a few times this hour. Wait a bit and try again.",
    VOICE_CLONE_FAILED: "Cartesia could not make the voice. Your notes still use the standard voice.",
    VOICE_EXISTS: "You already have a voice. Delete it first to record a new one.",
    VOICE_DELETE_FAILED: "Could not delete the voice right now. Nothing was changed; try again.",
    SPEECH_NOT_CONFIGURED: "Voices are not set up on this server yet.",
  };
  return (code && known[code]) || "Something went wrong.";
};

/** Mono 16-bit PCM WAV at 24 kHz from any recording the browser can decode. */
async function toWav(recording: Blob): Promise<Blob> {
  const decoded = await new AudioContext().decodeAudioData(await recording.arrayBuffer());
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * RATE), RATE);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const pcm = (await offline.startRendering()).getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const text = (at: number, s: string) => [...s].forEach((c, i) => out.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, RATE, true); out.setUint32(28, RATE * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  text(36, "data"); out.setUint32(40, pcm.length * 2, true);
  pcm.forEach((x, i) => out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 0x7fff, true));
  return new Blob([out.buffer], { type: "audio/wav" });
}

export function MyVoice() {
  const mine = useQuery(api.myVoice, {});
  const uploadUrl = useMutation(api.voiceUploadUrl);
  const create = useAction(api.createVoice);
  const remove = useAction(api.removeVoice);
  const [phase, setPhase] = useState<"idle" | "recording" | "review" | "saving">("idle");
  const [seconds, setSeconds] = useState(0);
  const [wav, setWav] = useState<Blob | null>(null);
  const [agree, setAgree] = useState(false);
  const [status, setStatus] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const started = useRef(0);
  const preview = useMemo(() => (wav ? URL.createObjectURL(wav) : null), [wav]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  useEffect(() => {
    if (phase !== "recording") return;
    const timer = setInterval(() => {
      const s = (Date.now() - started.current) / 1000;
      setSeconds(s);
      if (s >= MAX_S) recorder.current?.stop();
    }, 200);
    return () => clearInterval(timer);
  }, [phase]);

  if (mine === undefined) return null;

  const start = async () => {
    setStatus(""); setWav(null); setAgree(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        try { setWav(await toWav(new Blob(chunks, { type: rec.mimeType }))); setPhase("review"); }
        catch { setStatus("Could not read the recording. Try again."); setPhase("idle"); }
      };
      recorder.current = rec; started.current = Date.now(); setSeconds(0);
      rec.start(); setPhase("recording");
    } catch { setStatus("Microphone access is needed to record your voice."); }
  };

  const save = async () => {
    if (!wav || !agree) return;
    setPhase("saving"); setStatus("Making your voice with Cartesia…");
    try {
      const res = await fetch(await uploadUrl({}), { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
      const { storageId } = await res.json() as { storageId: string };
      await create({ storageId, consent: true });
      setWav(null); setPhase("idle");
      setStatus("Done. Your new notes, and the ones not heard yet, are now read in your voice. The recording was deleted.");
    } catch (e) { setPhase("review"); setStatus(voiceError(e)); }
  };

  const withdraw = async () => {
    if (!confirm("Delete your voice? It is deleted at Cartesia, and every note you sent goes back to the standard voice.")) return;
    setStatus("Deleting…");
    try {
      const r = await remove({});
      setStatus(r.removed ? `Deleted. ${r.rerendered} note${r.rerendered === 1 ? "" : "s"} will be read in the standard voice.` : "");
    } catch (e) { setStatus(voiceError(e)); }
  };

  if (mine.hasVoice) {
    return <section className="card"><h2>Your voice</h2>
      <p>Your notes are read in your own voice{mine.consentAt ? ` (since ${new Date(mine.consentAt).toLocaleDateString()})` : ""}.
        It is used for nothing else: not for directions, warnings from VisionOS, or anyone else's notes.</p>
      <p className="status">You agreed: “{mine.consentText}”</p>
      <button className="secondary" onClick={withdraw}>Delete my voice</button>
      <p className="status" aria-live="polite">{status}</p>
    </section>;
  }

  return <section className="card"><h2>Read your notes in your own voice</h2>
    <p>Instead of a computer voice, they can hear you. Read the two paragraphs below aloud, in your normal voice,
      somewhere quiet. It takes about 20 seconds.</p>
    <blockquote className="consent"><strong>{mine.consentText}</strong></blockquote>
    <blockquote className="consent">{mine.practiceText}</blockquote>
    <div className="row">
      {phase !== "recording"
        ? <button onClick={start} disabled={phase === "saving"}>{wav ? "Record again" : "Start recording"}</button>
        : <button onClick={() => recorder.current?.stop()} disabled={seconds < MIN_S}>
            {seconds < MIN_S ? `Recording… keep reading (${Math.ceil(MIN_S - seconds)} s)` : `Stop (${Math.round(seconds)} s)`}
          </button>}
    </div>
    {wav && phase !== "recording" && <>
      <audio controls src={preview ?? undefined} style={{ width: "100%", marginTop: ".6rem" }} />
      <label className="row" style={{ marginTop: ".6rem", fontWeight: 400 }}>
        <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} style={{ width: "auto" }} />
        This is my own voice, recorded just now, and I agree to the sentence I read.
      </label>
      <button onClick={save} disabled={!agree || phase === "saving"}>Use my voice for my notes</button>
    </>}
    <p className="status" aria-live="polite">{status}</p>
    <p className="status">Made with Cartesia Instant Voice Clone. The recording is deleted right after; only the voice is kept,
      and you can delete it any time. The person you help can also turn helpers' voices off.</p>
  </section>;
}

export function Listening() {
  const prefs = useQuery(api.prefs, {});
  const save = useMutation(api.setPrefs);
  const [draft, setDraft] = useState<{ speed: number; volume: number; helperVoices: boolean } | null>(null);
  const [status, setStatus] = useState("");
  if (!prefs) return null;
  const value = draft ?? prefs;
  const set = (patch: Partial<typeof value>) => setDraft({ ...value, ...patch });
  const apply = async () => {
    setStatus("Saving…");
    try {
      const n = await save(value);
      setDraft(null);
      setStatus(n ? `Saved. ${n} new note${n === 1 ? " is" : "s are"} being re-read with these settings.` : "Saved.");
    } catch { setStatus("Could not save."); }
  };
  return <section className="card"><h2>How notes sound</h2>
    <label htmlFor="speed">Speed: {value.speed.toFixed(2)}×</label>
    <input id="speed" type="range" min={prefs.speedRange[0]} max={prefs.speedRange[1]} step={0.05} value={value.speed}
      onChange={(e) => set({ speed: Number(e.target.value) })} />
    <label htmlFor="volume">Volume: {value.volume.toFixed(2)}×</label>
    <input id="volume" type="range" min={prefs.volumeRange[0]} max={prefs.volumeRange[1]} step={0.05} value={value.volume}
      onChange={(e) => set({ volume: Number(e.target.value) })} />
    <label className="row" style={{ fontWeight: 400 }}>
      <input type="checkbox" checked={value.helperVoices} onChange={(e) => set({ helperVoices: e.target.checked })} style={{ width: "auto" }} />
      Read notes in my helpers' own voices (when they've recorded one)
    </label>
    <button onClick={apply} disabled={!draft}>Save</button>
    <p className="status" aria-live="polite">{status}</p>
  </section>;
}
