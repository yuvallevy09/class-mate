import { sendCourseChat } from "./chat";
import { ensureCsrf, getApiBaseUrl } from "./http";
import { normalizeCitationMarkers } from "@/components/chat/citations";

/**
 * Chat-stream adapter. Components/hooks only ever talk to this.
 *
 * Default: a real SSE adapter over POST /chat-v2/stream (token-by-token).
 * Fallback (VITE_CHAT_STREAMING=false): the legacy blocking POST /chat-v2 that
 * synthesizes a couple of events. Both implement the same handler contract:
 *
 * startChatTurn({ courseId, message, conversationId }, handlers) -> { cancel() }
 * handlers:
 *   onStatus({ stage, label })  stage: "searching" | "reading" | "generating"
 *   onThinkingDelta(text)       appended reasoning text
 *   onAnswerDelta(text)         appended answer text (RAW [N] markers; the UI normalizes)
 *   onCitations(citations)      ChatCitation[]
 *   onDone({ conversationId, fullText, citations, thoughtForSecs })
 *   onError(err)                preserves .status/.data from http.js errors
 */
export function startChatTurn(args, handlers = {}) {
  const streaming =
    String(import.meta.env?.VITE_CHAT_STREAMING ?? "true")
      .trim()
      .toLowerCase() !== "false";
  return streaming ? _startStreaming(args, handlers) : _startBlocking(args, handlers);
}

// --- Real SSE adapter -------------------------------------------------------

function _startStreaming({ courseId, message, conversationId }, handlers = {}) {
  let cancelled = false;
  const controller = new AbortController();
  const safe = (fn, ...a) => {
    if (!cancelled && typeof fn === "function") fn(...a);
  };
  const t0 = performance.now();
  let citations = [];

  const dispatch = (frame) => {
    switch (frame?.type) {
      case "status":
        safe(handlers.onStatus, { stage: frame.stage, label: frame.label ?? null });
        break;
      case "thinking":
        safe(handlers.onThinkingDelta, String(frame.delta ?? ""));
        break;
      case "citations":
        citations = Array.isArray(frame.citations) ? frame.citations : [];
        safe(handlers.onCitations, citations);
        break;
      case "answer":
        safe(handlers.onAnswerDelta, String(frame.delta ?? ""));
        break;
      case "done":
        safe(handlers.onDone, {
          conversationId: frame.conversationId || null,
          // The backend hands us the exact persisted (link-formatted) text so
          // the live turn converges to the persisted message (see useAssistantTurn).
          fullText: typeof frame.text === "string" ? frame.text : "",
          citations,
          thoughtForSecs: Math.max(1, Math.round((performance.now() - t0) / 1000)),
        });
        break;
      case "error":
        safe(handlers.onError, new Error(frame.message || "Streaming error"));
        break;
      default:
        break;
    }
  };

  (async () => {
    try {
      const csrf = await ensureCsrf();
      const base = getApiBaseUrl().replace(/\/+$/, "");
      const url = `${base}/api/v1/courses/${encodeURIComponent(courseId)}/chat-v2/stream`;
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ message: String(message), conversationId: conversationId ?? null }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        // Pre-stream guard errors (401/403/404/422) come back as JSON, mirroring http.js.
        let data = null;
        try {
          data = await res.json();
        } catch {
          data = null;
        }
        const err = new Error(`HTTP ${res.status}`);
        err.status = res.status;
        err.data = data;
        throw err;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!block.startsWith("data:")) continue;
          try {
            dispatch(JSON.parse(block.slice(5).trim()));
          } catch {
            // Skip malformed frame; keep streaming.
          }
        }
      }
    } catch (err) {
      if (cancelled || err?.name === "AbortError") return;
      safe(handlers.onError, err);
    }
  })();

  return {
    cancel() {
      cancelled = true;
      controller.abort();
    },
  };
}

// --- Legacy blocking fallback (VITE_CHAT_STREAMING=false) --------------------

function _startBlocking({ courseId, message, conversationId }, handlers = {}) {
  let cancelled = false;
  const safe = (fn, ...args) => {
    if (!cancelled && typeof fn === "function") fn(...args);
  };

  const t0 = performance.now();
  safe(handlers.onStatus, { stage: "searching", label: "Searching course materials…" });

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
