import { useEffect, useRef, useState } from "react";

const MARKER_RE = /\[\d{1,3}\]\(#cm-(?:cite|src)-\d{1,3}\)/g;

function atomicSpans(text) {
  const spans = [];
  for (const m of text.matchAll(MARKER_RE)) {
    spans.push({ start: m.index, end: m.index + m[0].length });
  }
  return spans;
}

/**
 * Typewriter reveal over a (possibly growing) normalized text.
 * Citation markers are revealed atomically so half-typed link syntax never
 * renders. Pacing: ~`cps` chars/sec with a short hold after sentence ends.
 * Respects prefers-reduced-motion (instant reveal).
 */
export function useTypewriter(fullText, { enabled = false, cps = 150, onReveal, onComplete } = {}) {
  const text = String(fullText ?? "");
  const [revealed, setRevealed] = useState(() => (enabled ? 0 : text.length));
  const posRef = useRef(enabled ? 0 : text.length);
  const callbacksRef = useRef({});
  callbacksRef.current = { onReveal, onComplete };

  useEffect(() => {
    if (!enabled) {
      posRef.current = text.length;
      setRevealed(text.length);
      return undefined;
    }

    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    if (reduced) {
      posRef.current = text.length;
      setRevealed(text.length);
      callbacksRef.current.onComplete?.();
      return undefined;
    }

    if (posRef.current >= text.length) {
      if (text.length > 0) callbacksRef.current.onComplete?.();
      return undefined;
    }

    const spans = atomicSpans(text);
    let raf = 0;
    let last = performance.now();
    let hold = 0;

    const tick = (now) => {
      const dt = Math.min(100, now - last);
      last = now;
      if (hold > 0) {
        hold -= dt;
      } else {
        let budget = (dt / 1000) * cps;
        let pos = posRef.current;
        while (budget >= 1 && pos < text.length) {
          pos += 1;
          for (const s of spans) {
            if (pos > s.start && pos < s.end) {
              pos = s.end;
              break;
            }
          }
          budget -= 1;
          const ch = text[pos - 1];
          if (ch && ".!?".includes(ch)) {
            hold = 160;
            break;
          }
        }
        if (pos !== posRef.current) {
          posRef.current = pos;
          setRevealed(pos);
          callbacksRef.current.onReveal?.();
        }
      }
      if (posRef.current < text.length) {
        raf = requestAnimationFrame(tick);
      } else {
        callbacksRef.current.onComplete?.();
      }
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text, enabled, cps]);

  return {
    visibleText: enabled ? text.slice(0, revealed) : text,
    done: revealed >= text.length,
  };
}
