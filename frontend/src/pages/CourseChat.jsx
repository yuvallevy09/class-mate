import React, { useState, useRef, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { getCourse } from "@/api/courses";
import { listConversationMessages } from "@/api/chat";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowUp, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "@/components/ui/use-toast";
import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";
import UserMessage from "@/components/chat/UserMessage";
import AssistantMessage from "@/components/chat/AssistantMessage";
import ThinkingDisclosure from "@/components/chat/ThinkingDisclosure";
import { normalizeCitationMarkers } from "@/components/chat/citations";
import { useAssistantTurn } from "@/hooks/useAssistantTurn";

export default function CourseChat() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const courseId = searchParams.get("id");
  const conversationId = searchParams.get("conversationId");
  const chatEnabled =
    String(import.meta.env.VITE_CHAT_ENABLED ?? "")
      .trim()
      .toLowerCase() === "true";

  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [optimisticMessages, setOptimisticMessages] = useState([]);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const [sendError, setSendError] = useState(null);
  const [videoPlayer, setVideoPlayer] = useState({ open: false, url: null, title: null });
  const scrollContainerRef = useRef(null);
  const textareaRef = useRef(null);
  const shouldAutoScrollRef = useRef(true);
  shouldAutoScrollRef.current = shouldAutoScroll;

  const queryClient = useQueryClient();

  const { data: _course } = useQuery({
    queryKey: ["course", courseId],
    queryFn: async () => getCourse(courseId),
    enabled: !!courseId,
  });

  const { data: messages = [] } = useQuery({
    queryKey: ["conversationMessages", conversationId],
    queryFn: () => listConversationMessages(conversationId),
    enabled: !!conversationId,
  });

  const activeConversationId = useMemo(
    () => (conversationId ? String(conversationId) : null),
    [conversationId]
  );

  const isNearBottom = () => {
    const el = scrollContainerRef.current;
    if (!el) return true;
    const thresholdPx = 120;
    const distanceFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight);
    return distanceFromBottom < thresholdPx;
  };

  const scrollToBottom = (behavior = "auto") => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const top = el.scrollHeight;
    try {
      el.scrollTo({ top, behavior });
    } catch {
      el.scrollTop = top;
    }
  };

  const handleBeforeSend = useCallback(({ tempId, text }) => {
    setShouldAutoScroll(true);
    setSendError(null);
    setMessage("");
    setOptimisticMessages((prev) => [
      ...prev,
      { id: tempId, role: "user", content: text, optimistic: true },
    ]);
    requestAnimationFrame(() => scrollToBottom("smooth"));
  }, []);

  const handlePersisted = useCallback(
    (info) => {
      const newConversationId =
        (typeof info?.conversationId === "string" && info.conversationId) || null;
      const resolvedConversationId = newConversationId || activeConversationId;

      // If this started a new conversation, reflect it in the URL so refresh/share works.
      if (!activeConversationId && newConversationId) {
        navigate(
          createPageUrl(
            `CourseChat?id=${courseId}&conversationId=${encodeURIComponent(newConversationId)}`
          ),
          { replace: true }
        );
      }

      queryClient.invalidateQueries({ queryKey: ["conversations", courseId] });
      if (resolvedConversationId) {
        queryClient.invalidateQueries({
          queryKey: ["conversationMessages", resolvedConversationId],
        });
      }
    },
    [activeConversationId, courseId, navigate, queryClient]
  );

  const handleSendError = useCallback((err, { tempId, text }) => {
    if (tempId) {
      setOptimisticMessages((prev) => prev.filter((m) => m.id !== tempId));
    }
    setMessage(text ?? "");
    requestAnimationFrame(() => textareaRef.current?.focus());

    const status = err?.status;
    const detail = err?.data?.detail;
    let msg = "Failed to send message. Please try again.";
    if (status === 501) {
      msg =
        (typeof detail === "string" && detail) ||
        "Chat is not configured on the server yet. Set GOOGLE_API_KEY in backend/.env.";
    } else if (status === 502) {
      msg = "The LLM request failed (502). Please retry.";
    } else if (status === 403) {
      msg = "Request blocked by CSRF. Refresh the page and try again.";
    }
    setSendError(msg);
    toast({
      title: "Chat error",
      description: msg,
    });
  }, []);

  const { turn, sendMessage, notifyRevealComplete, clearTurn, isPending } = useAssistantTurn({
    courseId,
    conversationId: activeConversationId,
    onBeforeSend: handleBeforeSend,
    onPersisted: handlePersisted,
    onError: handleSendError,
  });

  // Server messages, minus the assistant message that duplicates the live
  // turn (it appears after refetch while the typewriter may still be
  // revealing), plus optimistic user messages not yet on the server.
  const renderedMessages = useMemo(() => {
    let base = messages;
    if (turn?.answer) {
      base = base.filter(
        (m) =>
          !(
            m?.role === "assistant" &&
            normalizeCitationMarkers(String(m.content ?? ""), m.citations) === turn.answer
          )
      );
    }
    if (!optimisticMessages.length) return base;
    const existingUserContents = new Set(
      base
        .filter((m) => m?.role === "user" && typeof m?.content === "string")
        .map((m) => m.content)
    );
    return [...base, ...optimisticMessages.filter((m) => !existingUserContents.has(m.content))];
  }, [messages, optimisticMessages, turn]);

  // Once the live turn is fully revealed AND its persisted copy is in the
  // query cache, hand rendering over to the server message.
  useEffect(() => {
    if (!turn || turn.phase !== "done" || !turn.answer) return;
    const matched = messages.some(
      (m) =>
        m?.role === "assistant" &&
        normalizeCitationMarkers(String(m.content ?? ""), m.citations) === turn.answer
    );
    if (matched) clearTurn();
  }, [messages, turn, clearTurn]);

  const handleSend = () => {
    if (!chatEnabled) return;
    if (message.trim() && !isPending) {
      setShouldAutoScroll(true);
      sendMessage(message.trim());
    }
  };

  const handleKeyDown = (e) => {
    if (!chatEnabled) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    if (!shouldAutoScroll) return;
    requestAnimationFrame(() => scrollToBottom("smooth"));
  }, [renderedMessages.length, turn?.phase, shouldAutoScroll]);

  // Follow the typewriter reveal (called per animation frame by the live turn).
  const handleReveal = useCallback(() => {
    if (shouldAutoScrollRef.current) scrollToBottom("auto");
  }, []);

  // Once server messages include an optimistic user message, drop the local optimistic copy.
  useEffect(() => {
    if (!optimisticMessages.length) return;
    setOptimisticMessages((prev) => {
      const next = prev.filter(
        (o) =>
          !messages.some(
            (m) =>
              m?.role === "user" &&
              typeof m?.content === "string" &&
              m.content === o.content
          )
      );
      // Avoid infinite re-renders: if nothing changed, preserve the same array reference.
      return next.length === prev.length ? prev : next;
    });
  }, [messages, optimisticMessages.length]);

  const openVideoModal = useCallback(({ url, title }) => {
    setVideoPlayer({ open: true, url, title });
  }, []);

  return (
    <div className="h-screen supports-[height:100dvh]:h-[100dvh] overflow-hidden flex flex-col relative">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 right-0 w-[600px] h-[600px] bg-purple-500/5 rounded-full blur-[200px]" />
        <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-pink-500/5 rounded-full blur-[200px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen(!isSidebarOpen)} showMenu={true} />

      {/* Main Content */}
      <div className="flex-1 flex relative z-10 overflow-hidden min-h-0">
        {/* Chat Area */}
        <div className="flex-1 flex flex-col min-h-0 relative">
          <div
            ref={scrollContainerRef}
            onScroll={() => setShouldAutoScroll(isNearBottom())}
            className="flex-1 min-h-0 overflow-y-auto px-4 lg:px-8 pt-6"
          >
            <div className="max-w-3xl mx-auto space-y-6 pb-40">
              {messages.length === 0 && !turn && !optimisticMessages.length && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="text-center py-20"
                >
                  <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center mx-auto mb-6">
                    <Sparkles className="w-8 h-8 text-purple-400" />
                  </div>
                  <h3 className="text-xl font-semibold mb-2">Start a Conversation</h3>
                  <p className="text-gray-400 max-w-md mx-auto">
                    Ask questions about your course materials and get personalized help from your AI teaching assistant.
                  </p>
                </motion.div>
              )}

              <AnimatePresence>
                {renderedMessages.map((msg, index) => (
                  <motion.div
                    key={msg.id || index}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    {msg.role === "user" ? (
                      <UserMessage content={msg.content} />
                    ) : (
                      <div className="w-full">
                        <AssistantMessage
                          content={msg.content}
                          citations={msg.citations}
                          onOpenVideo={openVideoModal}
                        />
                      </div>
                    )}
                  </motion.div>
                ))}

                {turn && (
                  <motion.div
                    key={`live-${turn.id}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex justify-start"
                  >
                    <div className="w-full">
                      <ThinkingDisclosure
                        phase={turn.phase}
                        stage={turn.stage}
                        statusLabel={turn.statusLabel}
                        thinkingText={turn.thinkingText}
                        thoughtForSecs={turn.thoughtForSecs}
                      />
                      {turn.answer ? (
                        <AssistantMessage
                          content={turn.answer}
                          citations={turn.citations}
                          animate
                          onOpenVideo={openVideoModal}
                          onReveal={handleReveal}
                          onRevealComplete={notifyRevealComplete}
                        />
                      ) : null}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>

          {/* Floating input bar */}
          <div className="absolute inset-x-0 bottom-5 lg:bottom-7 z-20 px-4 pointer-events-none">
            <div className="w-full max-w-[640px] mx-auto pointer-events-auto">
              {!chatEnabled && (
                <div className="mb-2 text-center text-xs text-gray-400">
                  Chat is disabled (set <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono">VITE_CHAT_ENABLED=true</code> to enable).
                </div>
              )}
              {!!sendError && chatEnabled && (
                <div className="mb-2 text-center text-xs text-red-300/90">
                  {sendError}
                </div>
              )}
              <div className="flex items-end gap-2.5 rounded-[22px] border border-white/10 bg-[#16151C]/80 py-1.5 pl-[18px] pr-1.5 backdrop-blur-xl shadow-[0_16px_40px_rgba(0,0,0,0.5),0_0_0_1px_rgba(0,0,0,0.25)]">
                <Textarea
                  ref={textareaRef}
                  placeholder="Ask about your course..."
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={!chatEnabled}
                  className="flex-1 bg-transparent border-0 px-0 text-white placeholder:text-gray-500 resize-none min-h-[40px] max-h-[120px] focus-visible:ring-0"
                  rows={1}
                />
                <Button
                  onClick={handleSend}
                  disabled={!chatEnabled || !message.trim() || isPending}
                  className="btn-gradient rounded-full h-9 w-9 p-0 shrink-0 mb-[3px]"
                >
                  <ArrowUp className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>

        <CourseSidebar
          courseId={courseId}
          isOpen={isSidebarOpen}
          onClose={() => setIsSidebarOpen(false)}
          activeConversationId={activeConversationId}
        />
      </div>

      <Dialog
        open={!!videoPlayer.open}
        onOpenChange={(open) => setVideoPlayer((prev) => ({ ...prev, open }))}
      >
        <DialogContent className="bg-[#131313] border-white/10 text-white max-w-5xl">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">
              {videoPlayer.title || "Video"}
            </DialogTitle>
          </DialogHeader>
          <div className="mt-3">
            <div className="rounded-2xl overflow-hidden bg-black/60 border border-white/10">
              <video
                key={videoPlayer.url || "video"}
                src={videoPlayer.url || undefined}
                controls
                playsInline
                className="w-full max-h-[70vh] bg-black"
              />
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
