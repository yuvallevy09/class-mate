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
        title: g.title,
        snippet,
        chapterTitle:
          (typeof extra?.chapterTitle === "string" && extra.chapterTitle.trim()) || null,
        ranges: Array.from(g.ranges.values()),
      });
    } else {
      byIndex.set(i, {
        kind: "file",
        displayNumber: i,
        groupKey: `file:${i}`,
        title: getCitationTitle(c),
        snippet,
        pageLabel: getPageLabel(extra),
        url: typeof c?.url === "string" ? c.url : null,
      });
    }
  });

  return { byIndex };
}

const NORMALIZED_MARKER_RE = /\[(\d{1,3})\]\(#cm-cite-(\d{1,3})\)/g;

/**
 * Normalizes citation markers in message content to `[N](#cm-cite-N)`.
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

  // 3) Collapse whitespace-adjacent markers that resolve to the same group
  //    (the rewritten format emits one link per video range: ¹ᵃ¹ᵇ → one pill).
  const { byIndex } = buildCitationModel(citations);
  let out = "";
  let cursor = 0;
  let lastEnd = -1;
  let lastKey = null;
  for (const m of text.matchAll(NORMALIZED_MARKER_RE)) {
    const idx = Number(m[2]);
    const key = byIndex.get(idx)?.groupKey ?? `i:${idx}`;
    const adjacent = lastKey !== null && /^\s*$/.test(text.slice(lastEnd, m.index));
    if (adjacent && key === lastKey) {
      cursor = m.index + m[0].length;
      lastEnd = cursor;
      continue;
    }
    out += text.slice(cursor, m.index) + m[0];
    cursor = m.index + m[0].length;
    lastEnd = cursor;
    lastKey = key;
  }
  out += text.slice(cursor);
  return out;
}
