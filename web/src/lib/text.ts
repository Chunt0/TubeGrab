/** Text matching and A–Z grouping, the same way the server searches (db._search_text). */

/** Case and accents don't count: "Björk" → "bjork". */
export const fold = (s: string) => s.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

/** The words of a query, folded. */
export const words = (q: string) => fold(q).split(/\s+/).filter(Boolean);

/** Every word appears somewhere in `fields` (in any order; "acdc" also finds "AC/DC"). */
export function matches(tokens: string[], ...fields: (string | null | undefined)[]) {
  if (!tokens.length) return true;
  const hay = fold(fields.filter(Boolean).join(" "));
  const squashed = hay.replace(/[^\p{L}\p{N}]+/gu, "");
  return tokens.every((t) => hay.includes(t) || squashed.includes(t));
}

/** Better matches first: a field that starts with the query, then one with a word that does. */
export function rank(q: string, field: string) {
  const f = fold(field), t = fold(q.trim());
  return f.startsWith(t) ? 0 : f.includes(` ${t}`) ? 1 : 2;
}

/** A–Z heading for a name; digits and symbols go under "#". */
export function letter(s: string | null | undefined) {
  const c = fold(s ?? "").replace(/^[^\p{L}\p{N}]+/u, "").charAt(0).toUpperCase();
  return c >= "A" && c <= "Z" ? c : "#";
}

/** Sort names the way they're grouped: "#" first, then A–Z, accents ignored. */
export function byName(a: string | null | undefined, b: string | null | undefined) {
  const la = letter(a), lb = letter(b);
  if (la !== lb) return la === "#" ? -1 : lb === "#" ? 1 : la < lb ? -1 : 1;
  return (a ?? "").localeCompare(b ?? "", undefined, { sensitivity: "base", numeric: true });
}

export const decade = (year: string | null | undefined) => {
  const y = parseInt(year ?? "", 10);
  return y ? `${Math.floor(y / 10) * 10}s` : "Unknown year";
};
