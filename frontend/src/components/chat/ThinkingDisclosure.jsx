import React, { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTypewriter } from "./useTypewriter";

// Rotating status copy, keyed by stage. Every line is honest about what's
// actually happening (retrieval over lecture transcripts, then reasoning) —
// just with a bit of personality. The label cycles while the stage is active.
const SEARCHING_PHRASES = [
  "Searching your course materials…",
  "Checking the lectures…",
  "Leafing through the transcripts…",
  "Tracking down the relevant moments…",
  "Cross-referencing your lectures…",
  "Digging through the course…",
];
const THINKING_PHRASES = [
  "Thinking…",
  "Pondering…",
  "Connecting the dots…",
  "Piecing it together…",
  "Mulling it over…",
  "Reasoning it through…",
  "Untangling the threads…",
  "Percolating…",
];
const PHRASE_SWAP_MS = 2200;

function _phrasesFor(stage) {
  return stage === "generating" ? THINKING_PHRASES : SEARCHING_PHRASES;
}

/**
 * The thinking status line above an assistant reply.
 * Active: shimmer label (real stage label, or whimsy rotation while generating).
 * Settled (live turn): "Thought for Ns".
 * Persisted (history reload): no phase/seconds, just stored reasoning — renders a
 * static, expandable "Thought process" panel.
 * The expandable reasoning panel renders only when thinkingText exists.
 */
export default function ThinkingDisclosure({
  phase,
  stage,
  statusLabel,
  thinkingText,
  thoughtForSecs,
}) {
  const active = phase === "retrieving" || phase === "thinking";
  const settled = (phase === "answering" || phase === "done") && thoughtForSecs != null;
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [open, setOpen] = useState(false);

  // Cycle the phrase pool for the current stage while active. Reset on stage
  // change so searching↔thinking always starts from the pool's first line.
  useEffect(() => {
    if (!active) {
      setPhraseIdx(0);
      return undefined;
    }
    setPhraseIdx(0);
    const pool = _phrasesFor(stage);
    const timer = setInterval(
      () => setPhraseIdx((i) => (i + 1) % pool.length),
      PHRASE_SWAP_MS
    );
    return () => clearInterval(timer);
  }, [active, stage]);

  const hasThinking = !!(thinkingText && thinkingText.trim());

  // Reveal the reasoning letter-by-letter while it streams (active); once
  // settled/persisted, show it whole. A bit faster than the answer since the
  // reasoning is denser and secondary. Has no citation markers, so the
  // typewriter reveals plain characters.
  const { visibleText: revealedThinking } = useTypewriter(thinkingText || "", {
    enabled: active,
    cps: 240,
  });

  // Live turn (active/settled) always shows; a persisted message has neither
  // phase nor seconds, so it shows only when there's stored reasoning to expand.
  if (!active && !settled && !hasThinking) return null;

  const pool = _phrasesFor(stage);
  const label = active
    ? pool[phraseIdx % pool.length] || statusLabel || "Thinking…"
    : settled
      ? `Thought for ${thoughtForSecs}s`
      : "Thought process";

  const labelSpan = (
    <span key={label} className={active ? "cm-shimmer cm-word-in" : ""}>
      {label}
    </span>
  );

  return (
    <div className="mb-3">
      {hasThinking ? (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex items-center gap-1.5 text-[13px] font-medium text-gray-400 transition-colors hover:text-purple-200"
        >
          <ChevronRight
            className={`h-3.5 w-3.5 opacity-70 transition-transform duration-200 ${
              open ? "rotate-90" : ""
            }`}
          />
          {labelSpan}
        </button>
      ) : (
        <div className="text-[13px] font-medium text-gray-400">{labelSpan}</div>
      )}

      {hasThinking && (
        <div
          className={`grid transition-[grid-template-rows] duration-300 ${
            open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
          }`}
        >
          <div className="overflow-hidden">
            <div className="mt-2.5 whitespace-pre-wrap border-l-2 border-purple-500/30 pl-3.5 text-[13px] leading-relaxed text-gray-400">
              {revealedThinking}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
