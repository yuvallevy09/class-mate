// Pure helpers for chat citations: marker normalization + display model.
//
// Two marker formats must be supported:
//  1. Persisted/rewritten (current backend): `[¹ᵃ](#cm-src-1)` — display glyphs
//     are unreliable superscripts, so we key off the href index ONLY.
//  2. Plain (future backend): `[1]` — converted only when citation 1 exists.
// Both normalize to the stable internal form `[N](#cm-cite-N)` that the
// markdown `a` renderer turns into a CitationPill.

export function fmtTimestamp(seconds) {
  const s = Math.max(0, Number(seconds || 0));
  const mm = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

export function isVideoCitation(c) {
  const extra = c?.extra || {};
  const t = String(extra?.type || "").toLowerCase();
  const contentId = c?.content_id || c?.contentId || null;
  return t === "video" && !!contentId;
}

export function getCitationContentId(c) {
  return c?.content_id || c?.contentId || null;
}

export function getCitationTitle(c) {
  const extra = c?.extra || {};
  return (
    String(c?.title || "").trim() ||
    String(extra?.original_filename || "").trim() ||
    String(getCitationContentId(c) || "").trim() ||
    "Course content"
  );
}

export function isRedundantChapterTitle(t) {
  const s = String(t || "").trim();
  if (!s) return true;
  return s.toLowerCase() === "full lecture";
}

/** Chip label: "Lect 5: Backpropagation…" from the lectureSlug + title. */
function getChipLabel(extra, title) {
  const slug = String(extra?.lectureSlug || "").trim();
  const m = slug.match(/^L(\d+)$/i);
  return m ? `Lect ${m[1]}: ${title}` : title;
}

function getPageLabel(extra) {
  const ps = extra?.pageStart ?? extra?.page_start ?? null;
  const pe = extra?.pageEnd ?? extra?.page_end ?? null;
  if (!ps && !pe) return "";
  const a = Number(ps || pe);
  const b = Number(pe || ps);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return "";
  return a === b ? ` (p.${a})` : ` (p.${a}–${b})`;
}

/**
 * Builds the per-index display model for citation pills/popovers.
 * Video citations are grouped by content_id: every index of the same video
 * shares the group's displayNumber (its leader index) and full range list,
 * while keeping its own snippet/chapter for the popover.
 *
 * Returns { byIndex: Map<number, model> } where model is:
 *   { kind: "video", displayNumber, groupKey, title, snippet, chapterTitle,
 *     ranges: [{ s, e, url, chapterTitle }] }
 *   { kind: "file", displayNumber, groupKey, title, snippet, pageLabel, url }
 */
export function buildCitationModel(citations = []) {
  const items = Array.isArray(citations) ? citations : [];
  const videoGroups = new Map(); // contentId -> { leaderIndex, title, ranges: Map }

  items.forEach((c, idx) => {
    const i = idx + 1;
    if (!isVideoCitation(c)) return;

    const contentId = String(getCitationContentId(c));
    const extra = c?.extra || {};
    const start = Number(extra?.startSec || 0);
    const end = Number(extra?.endSec || start);
    const sInt = Math.max(0, Math.floor(start));
    const eInt = Math.max(0, Math.floor(end));
    const rngKey = `${sInt}-${eInt}`;

    if (!videoGroups.has(contentId)) {
      videoGroups.set(contentId, {
        contentId,
        leaderIndex: i,
        title: getCitationTitle(c),
        ranges: new Map(),
      });
    }
    const g = videoGroups.get(contentId);
    g.leaderIndex = Math.min(g.leaderIndex, i);
    if (!g.title) g.title = getCitationTitle(c);

    if (!g.ranges.has(rngKey)) {
      g.ranges.set(rngKey, {
        s: sInt,
        e: eInt,
        url: typeof c?.url === "string" ? c.url : null,
        chapterTitle:
          (typeof extra?.chapterTitle === "string" && extra.chapterTitle.trim()) || null,
      });
    }
    const r = g.ranges.get(rngKey);
    if (!r.url && typeof c?.url === "string") r.url = c.url;
    if (!r.chapterTitle && typeof extra?.chapterTitle === "string" && extra.chapterTitle.trim()) {
      r.chapterTitle = extra.chapterTitle.trim();
    }
  });

  const byIndex = new Map();
  items.forEach((c, idx) => {
    const i = idx + 1;
    const extra = c?.extra || {};
    const snippet = typeof c?.snippet === "string" && c.snippet.trim() ? c.snippet.trim() : null;
    if (isVideoCitation(c)) {
      const g = videoGroups.get(String(getCitationContentId(c)));
      byIndex.set(i, {
        kind: "video",
        displayNumber: g.leaderIndex,
        groupKey: `video:${g.contentId}`,
        contentId: g.contentId,
        title: g.title,
        chipLabel: getChipLabel(extra, g.title),
        snippet,
        chapterTitle:
          (typeof extra?.chapterTitle === "string" && extra.chapterTitle.trim()) || null,
        ranges: Array.from(g.ranges.values()),
      });
    } else {
      const title = getCitationTitle(c);
      byIndex.set(i, {
        kind: "file",
        displayNumber: i,
        groupKey: `file:${i}`,
        title,
        chipLabel: title,
        snippet,
        pageLabel: getPageLabel(extra),
        url: typeof c?.url === "string" ? c.url : null,
      });
    }
  });

  return { byIndex };
}

/**
 * Normalizes citation markers in message content to the stable internal form
 * `[N](#cm-cite-N)` — or, for runs of adjacent markers, one multi-source
 * marker `[N](#cm-cite-N-M-…)` whose href carries every distinct source.
 * Idempotent — safe to run on already-normalized text.
 */
export function normalizeCitationMarkers(content, citations = []) {
  let text = String(content ?? "");
  if (!text) return text;

  // 1) Persisted rewritten links — keyed off the href index only.
  text = text.replace(
    /\[[^\]\n]{0,24}\]\(#cm-src-(\d{1,3})\)/g,
    (_m, n) => `[${n}](#cm-cite-${n})`
  );

  // 2) Plain [N] markers (future format) — only when citation N exists.
  //    Negative lookahead skips already-linked markers; the preceding-char
  //    guard skips things like `arr[0]` and reference-style links `[x][1]`.
  //    Looped until stable: the guard consumes a char, so adjacent runs like
  //    `[1][2][3]` convert one marker per pass.
  const count = Array.isArray(citations) ? citations.length : 0;
  if (count > 0) {
    let prev;
    do {
      prev = text;
      text = text.replace(/(^|[^\w\]])\[(\d{1,3})\](?!\()/g, (m, pre, n) => {
        const i = Number(n);
        return i >= 1 && i <= count ? `${pre}[${n}](#cm-cite-${n})` : m;
      });
    } while (text !== prev);
  }

  // 3) Chips cite the sentence they follow: move any marker run that
  //    directly precedes sentence punctuation to just after it.
  text = text.replace(
    /[ \t]*((?:\[\d{1,3}\]\(#cm-cite-[\d-]+\)[ \t]*)+)([.!?])/g,
    (_m, markers, punct) => `${punct} ${markers.trim()}`
  );

  // 4) Merge whitespace-adjacent markers into ONE multi-source marker
  //    `[N](#cm-cite-N-M-…)`, deduping sources that resolve to the same
  //    group (e.g. two ranges of the same video). Distinct sources page
  //    inside the popover (the chip shows "+N").
  const { byIndex } = buildCitationModel(citations);
  const markerRe = /\[\d{1,3}\]\(#cm-cite-([\d-]+)\)/g;
  let out = "";
  let cursor = 0;
  let run = null; // { indices: number[], keys: Set<string> }
  const flushRun = () => {
    if (!run) return;
    out += `[${run.indices[0]}](#cm-cite-${run.indices.join("-")})`;
    run = null;
  };
  for (const m of text.matchAll(markerRe)) {
    const idxs = m[1]
      .split("-")
      .map(Number)
      .filter((n) => Number.isFinite(n) && n >= 1);
    if (!idxs.length) continue;
    const between = text.slice(cursor, m.index);
    if (run && /^\s*$/.test(between)) {
      // extend the run; whitespace between merged markers is dropped
    } else {
      flushRun();
      out += between;
      run = { indices: [], keys: new Set() };
    }
    for (const i of idxs) {
      const key = byIndex.get(i)?.groupKey ?? `i:${i}`;
      if (!run.keys.has(key)) {
        run.keys.add(key);
        run.indices.push(i);
      }
    }
    cursor = m.index + m[0].length;
  }
  flushRun();
  out += text.slice(cursor);
  return out;
}

/** Parses a normalized marker href (`#cm-cite-1-3` / `#cm-src-2`) to indices. */
export function parseCiteHref(href) {
  const m = String(href || "").match(/^#cm-(?:cite|src)-([\d-]+)$/);
  if (!m) return null;
  const idxs = m[1]
    .split("-")
    .map(Number)
    .filter((n) => Number.isFinite(n) && n >= 1);
  return idxs.length ? idxs : null;
}
