import { sendCourseChat } from "./chat";
import { normalizeCitationMarkers } from "@/components/chat/citations";

/**
 * Chat-stream adapter interface. Components/hooks only ever talk to this;
 * the stub below wraps the blocking POST /chat-v2 and synthesizes events.
 * A future real SSE adapter implements the same handler contract and swaps
 * in here without any component changes.
 *
 * startChatTurn({ courseId, message, conversationId }, handlers) -> { cancel() }
 * handlers:
 *   onStatus({ stage, label })  stage: "searching" | "reading" | "generating"
 *   onThinkingDelta(text)       appended reasoning text (stub: never called)
 *   onAnswerDelta(text)         appended NORMALIZED answer text (pacing is UI-side)
 *   onCitations(citations)      ChatCitation[]
 *   onDone({ conversationId, fullText, citations, thoughtForSecs })
 *   onError(err)                preserves .status/.data from http.js errors
 */
export function startChatTurn({ courseId, message, conversationId }, handlers = {}) {
  let cancelled = false;
  const safe = (fn, ...args) => {
    if (!cancelled && typeof fn === "function") fn(...args);
  };

  const t0 = performance.now();
  safe(handlers.onStatus, { stage: "searching", label: "Searching course materials…" });

  // Per-lecture "Reading…" statuses are unknowable before the blocking POST
  // returns; if the request drags, fall through to the generic generating
  // stage (whimsy rotation). The real SSE adapter replaces this with honest
  // per-stage events.
  const generatingTimer = setTimeout(() => {
    safe(handlers.onStatus, { stage: "generating", label: null });
  }, 2500);

  (async () => {
    try {
      const res = await sendCourseChat({ courseId, message, conversationId });
      clearTimeout(generatingTimer);
      const citations = Array.isArray(res?.citations) ? res.citations : [];
      const normalized = normalizeCitationMarkers(String(res?.text ?? ""), citations);
      safe(handlers.onCitations, citations);
      // Non-streamed: the backend computes the reasoning in one shot, so we
      // deliver it as a single delta just before the answer reveals. A real SSE
      // adapter would emit these progressively as the cascade runs.
      if (res?.thinking) safe(handlers.onThinkingDelta, String(res.thinking));
      safe(handlers.onAnswerDelta, normalized);
      safe(handlers.onDone, {
        conversationId: res?.conversationId || res?.conversation_id || null,
        fullText: normalized,
        citations,
        thoughtForSecs: Math.max(1, Math.round((performance.now() - t0) / 1000)),
      });
    } catch (err) {
      clearTimeout(generatingTimer);
      safe(handlers.onError, err);
    }
  })();

  return {
    cancel() {
      cancelled = true;
      clearTimeout(generatingTimer);
    },
  };
}
