// Asymmetric matching of family notes to what the camera just found.
// A wrong match is far worse for reassurance ("this is candy") than for a warning
// ("this is bleach"), so the two kinds have different bars:
//   warning      -> any meaningful label word appears in the note
//   reassurance  -> every meaningful label word appears AND the note's place equals the query place
// Matching is lexical on purpose: Convex search ranks results but exposes no score, and an
// explainable rule is easier to defend. Reassurance is always spoken as "According to <name>'s note".

const STOP = new Set(["the", "and", "for", "with", "this", "that", "your", "you", "are", "is", "a", "an", "of", "on", "in",
  "to", "my", "his", "her", "its", "it", "one", "some", "left", "right", "top", "bottom", "small", "large", "big"]);

export function labelWords(label: string): string[] {
  const words = label.toLowerCase().replace(/\([^)]*\)/g, " ").split(/[^a-z0-9]+/).filter((w) => w.length >= 3 && !STOP.has(w));
  return [...new Set(words)];
}

function noteWords(text: string): Set<string> {
  return new Set(text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).map((w) => w.replace(/s$/, "")));
}

export type MatchKind = "warning" | "reassurance";

export function matchNote(note: { kind: "info" | "warning"; text: string; place?: string },
  words: string[], place?: string): MatchKind | null {
  if (words.length === 0) return null;
  const have = noteWords(note.text);
  const hit = (w: string) => have.has(w) || have.has(w.replace(/s$/, ""));
  if (note.kind === "warning") return words.some(hit) ? "warning" : null;
  const samePlace = place !== undefined && note.place !== undefined &&
    note.place.trim().toLowerCase() === place.trim().toLowerCase();
  return samePlace && words.every(hit) ? "reassurance" : null;
}
