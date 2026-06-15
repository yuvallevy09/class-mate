import { useCallback, useEffect, useRef, useState } from "react";
import { startChatTurn } from "@/api/chatStream";
import { normalizeCitationMarkers } from "@/components/chat/citations";

/**
 * Owns the live assistant turn:
 *   idle → retrieving → thinking → answering → done (error from any state).
 *
 * The turn finalizes (phase "done") only when BOTH the adapter finished and
 * the typewriter reveal caught up, so React Query refetches can't snap the
 * streamed message to full text mid-reveal.
 */
export function useAssistantTurn({
  courseId,
  conversationId,
  getViewing,
  onBeforeSend,
  onPersisted,
  onError,
}) {
  const [turn, setTurn] = useState(null);
  const handleRef = useRef(null);
  const startedAtRef = useRef(0);
  const doneInfoRef = useRef(null);
  const revealDoneRef = useRef(false);

  const cbRef = useRef({});
  cbRef.current = { onBeforeSend, onPersisted, onError, getViewing };

  useEffect(() => () => handleRef.current?.cancel(), []);

  const finalizeIfReady = useCallback(() => {
    if (!doneInfoRef.current || !revealDoneRef.current) return;
    const info = doneInfoRef.current;
    setTurn((prev) =>
      prev
        ? { ...prev, phase: "done", thoughtForSecs: info.thoughtForSecs ?? prev.thoughtForSecs }
        : prev
    );
  }, []);

  const sendMessage = useCallback(
    (text) => {
      const tempId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `temp-${Date.now()}-${Math.random().toString(16).slice(2)}`;

      cbRef.current.onBeforeSend?.({ tempId, text });

      startedAtRef.current = performance.now();
      doneInfoRef.current = null;
      revealDoneRef.current = false;
      setTurn({
        id: tempId,
        phase: "retrieving",
        stage: "searching",
        statusLabel: "Searching course materials…",
        thinkingText: "",
        answer: "",
        citations: [],
        thoughtForSecs: null,
      });

      // Read viewing context at send time so the timestamp reflects the live
      // playback head (video chat); null/undefined for course chat.
      const viewing = cbRef.current.getViewing?.() ?? null;

      handleRef.current?.cancel();
      handleRef.current = startChatTurn(
        { courseId, message: text, conversationId, viewing },
        {
          onStatus: ({ stage, label }) =>
            setTurn((prev) =>
              prev
                ? {
                    ...prev,
                    stage,
                    statusLabel: label ?? prev.statusLabel,
                    phase: stage === "generating" ? "thinking" : "retrieving",
                  }
                : prev
            ),
          onThinkingDelta: (delta) =>
            setTurn((prev) =>
              prev ? { ...prev, thinkingText: prev.thinkingText + delta } : prev
            ),
          onCitations: (citations) =>
            setTurn((prev) => (prev ? { ...prev, citations } : prev)),
          onAnswerDelta: (delta) =>
            setTurn((prev) => {
              if (!prev) return prev;
              const secs =
                prev.thoughtForSecs ??
                Math.max(1, Math.round((performance.now() - startedAtRef.current) / 1000));
              return { ...prev, phase: "answering", answer: prev.answer + delta, thoughtForSecs: secs };
            }),
          onDone: (info) => {
            doneInfoRef.current = info;
            // Converge the (raw-streamed) live answer to the exact persisted,
            // link-formatted text the server returns, so the live→persisted
            // handoff dedup in CourseChat matches after normalization. Falls
            // back to the already-accumulated answer when fullText is absent.
            if (info?.fullText) {
              setTurn((prev) =>
                prev
                  ? {
                      ...prev,
                      answer: normalizeCitationMarkers(
                        info.fullText,
                        info.citations ?? prev.citations
                      ),
                    }
                  : prev
              );
            }
            cbRef.current.onPersisted?.(info);
            finalizeIfReady();
          },
          onError: (err) => {
            setTurn(null);
            cbRef.current.onError?.(err, { tempId, text });
          },
        }
      );
    },
    [courseId, conversationId, finalizeIfReady]
  );

  const notifyRevealComplete = useCallback(() => {
    revealDoneRef.current = true;
    finalizeIfReady();
  }, [finalizeIfReady]);

  const clearTurn = useCallback(() => setTurn(null), []);

  // Abort any in-flight stream AND drop the live turn. Use when the turn should
  // be discarded outright (e.g. navigating away / switching context), so the
  // adapter's onDone/onPersisted can't fire after the fact.
  const cancel = useCallback(() => {
    handleRef.current?.cancel();
    setTurn(null);
  }, []);

  return {
    turn,
    sendMessage,
    notifyRevealComplete,
    clearTurn,
    cancel,
    isPending: !!turn && turn.phase !== "done",
  };
}
