import { useCallback, useEffect, useRef, useState } from "react";
import { startChatTurn } from "@/api/chatStream";

/**
 * Owns the live assistant turn:
 *   idle → retrieving → thinking → answering → done (error from any state).
 *
 * The turn finalizes (phase "done") only when BOTH the adapter finished and
 * the typewriter reveal caught up, so React Query refetches can't snap the
 * streamed message to full text mid-reveal.
 */
export function useAssistantTurn({ courseId, conversationId, onBeforeSend, onPersisted, onError }) {
  const [turn, setTurn] = useState(null);
  const handleRef = useRef(null);
  const startedAtRef = useRef(0);
  const doneInfoRef = useRef(null);
  const revealDoneRef = useRef(false);

  const cbRef = useRef({});
  cbRef.current = { onBeforeSend, onPersisted, onError };

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

      handleRef.current?.cancel();
      handleRef.current = startChatTurn(
        { courseId, message: text, conversationId },
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

  return {
    turn,
    sendMessage,
    notifyRevealComplete,
    clearTurn,
    isPending: !!turn && turn.phase !== "done",
  };
}
