import React, { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";

// Whimsical verbs are reserved for the long generic generation stage,
// rotating in fixed order. Real retrieval stages show honest labels.
const WHIMSY = ["Thinking", "Pondering", "Marinating", "Percolating"];
const WHIMSY_SWAP_MS = 2000;

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
  const [whimsyIdx, setWhimsyIdx] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!(active && stage === "generating")) {
      setWhimsyIdx(0);
      return undefined;
    }
    const timer = setInterval(
      () => setWhimsyIdx((i) => (i + 1) % WHIMSY.length),
      WHIMSY_SWAP_MS
    );
    return () => clearInterval(timer);
  }, [active, stage]);

  const hasThinking = !!(thinkingText && thinkingText.trim());

  // Live turn (active/settled) always shows; a persisted message has neither
  // phase nor seconds, so it shows only when there's stored reasoning to expand.
  if (!active && !settled && !hasThinking) return null;

  const label = active
    ? stage === "generating"
      ? `${WHIMSY[whimsyIdx]}…`
      : statusLabel || "Thinking…"
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
              {thinkingText}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
