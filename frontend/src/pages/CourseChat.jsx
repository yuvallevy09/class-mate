import React, { useState, useRef, useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { client } from "@/api/client";
import { listConversationMessages, sendCourseChat } from "@/api/chat";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Sparkles, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/use-toast";
import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";

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
  const [isTyping, setIsTyping] = useState(false);
  const [optimisticMessages, setOptimisticMessages] = useState([]);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const [sendError, setSendError] = useState(null);
  const scrollContainerRef = useRef(null);
  const textareaRef = useRef(null);
  
  const queryClient = useQueryClient();

  const { data: _course } = useQuery({
    queryKey: ['course', courseId],
    queryFn: async () => {
      const courses = await client.entities.Course.filter({ id: courseId });
      return courses[0];
    },
    enabled: !!courseId
  });

  const { data: messages = [] } = useQuery({
    queryKey: ['conversationMessages', conversationId],
    queryFn: () => listConversationMessages(conversationId),
    enabled: !!conversationId
  });

  const activeConversationId = useMemo(() => (conversationId ? String(conversationId) : null), [conversationId]);

  const renderedMessages = useMemo(() => {
    if (!optimisticMessages.length) return messages;
    const existingUserContents = new Set(
      messages
        .filter((m) => m?.role === "user" && typeof m?.content === "string")
        .map((m) => m.content)
    );
    const filteredOptimistic = optimisticMessages.filter(
      (m) => !existingUserContents.has(m.content)
    );
    return [...messages, ...filteredOptimistic];
  }, [messages, optimisticMessages]);

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

  const sendMessageMutation = useMutation({
    mutationFn: async (userMessage) => {
      return await sendCourseChat({
        courseId,
        message: userMessage,
        conversationId: activeConversationId,
      });
    },
    onMutate: (userMessage) => {
      const tempId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `temp-${Date.now()}-${Math.random().toString(16).slice(2)}`;

      const draftBeforeSend = message;

      setShouldAutoScroll(true);
      setIsTyping(true);
      setSendError(null);
      setMessage("");
      setOptimisticMessages((prev) => [
        ...prev,
        { id: tempId, role: "user", content: userMessage, optimistic: true },
      ]);

      requestAnimationFrame(() => scrollToBottom("smooth"));

      return { tempId, draftBeforeSend };
    },
    onSuccess: (response) => {
      const newConversationId =
        (typeof response?.conversationId === "string" && response.conversationId) ||
        (typeof response?.conversation_id === "string" && response.conversation_id) ||
        null;

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

      queryClient.invalidateQueries({ queryKey: ['conversations', courseId] });
      if (resolvedConversationId) {
        queryClient.invalidateQueries({
          queryKey: ["conversationMessages", resolvedConversationId],
        });
      }

      setOptimisticMessages([]);
      setIsTyping(false);
    },
    onError: (_err, userMessage, ctx) => {
      setIsTyping(false);
      if (ctx?.tempId) {
        setOptimisticMessages((prev) => prev.filter((m) => m.id !== ctx.tempId));
      }
      setMessage(ctx?.draftBeforeSend ?? userMessage ?? "");
      requestAnimationFrame(() => textareaRef.current?.focus());

      const status = _err?.status;
      const detail = _err?.data?.detail;
      let msg = "Failed to send message. Please try again.";
      if (status === 501) {
        msg =
          (typeof detail === "string" && detail) ||
          "Chat is not configured on the server yet. Set GOOGLE_API_KEY or GEMINI_API_KEY in backend/.env.";
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
    }
  });

  const handleSend = () => {
    if (!chatEnabled) return;
    if (message.trim() && !sendMessageMutation.isPending) {
      setShouldAutoScroll(true);
      sendMessageMutation.mutate(message.trim());
    }
  };

  const handleKeyDown = (e) => {
    if (!chatEnabled) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    if (!shouldAutoScroll) return;
    requestAnimationFrame(() => scrollToBottom("smooth"));
  }, [renderedMessages.length, isTyping, shouldAutoScroll]);

  // Once server messages include an optimistic user message, drop the local optimistic copy.
  useEffect(() => {
    if (!optimisticMessages.length) return;
    setOptimisticMessages((prev) =>
      prev.filter(
        (o) =>
          !messages.some(
            (m) =>
              m?.role === "user" &&
              typeof m?.content === "string" &&
              m.content === o.content
          )
      )
    );
  }, [messages, optimisticMessages.length]);

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
              {messages.length === 0 && !isTyping && (
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
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div className={`max-w-[85%] ${msg.role === 'user' ? 'order-1' : ''}`}>
                      <div
                        className={`rounded-2xl px-5 py-3 ${
                          msg.role === 'user'
                            ? 'bg-gradient-to-r from-pink-500 via-purple-500 to-blue-500 text-white'
                            : 'glass-card text-gray-100'
                        }`}
                      >
                        {msg.role === "user" ? (
                          <p className="text-sm lg:text-base leading-relaxed whitespace-pre-wrap">
                            {msg.content}
                          </p>
                        ) : (
                          <div className="text-sm lg:text-base leading-relaxed">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                p: ({ children }) => (
                                  <p className="my-2 whitespace-pre-wrap">{children}</p>
                                ),
                                strong: ({ children }) => (
                                  <strong className="font-semibold">{children}</strong>
                                ),
                                em: ({ children }) => <em className="italic">{children}</em>,
                                ul: ({ children }) => (
                                  <ul className="my-2 ml-5 list-disc space-y-1">{children}</ul>
                                ),
                                ol: ({ children }) => (
                                  <ol className="my-2 ml-5 list-decimal space-y-1">{children}</ol>
                                ),
                                li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                                a: ({ children, href }) => (
                                  <a
                                    href={href}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
                                  >
                                    {children}
                                  </a>
                                ),
                                blockquote: ({ children }) => (
                                  <blockquote className="my-3 border-l-2 border-white/15 pl-4 text-gray-200/90">
                                    {children}
                                  </blockquote>
                                ),
                                code: ({ className, children }) => {
                                  const isBlock = String(className || "").includes("language-");
                                  if (isBlock) {
                                    // The enclosing <pre> is handled below.
                                    return <code className={className}>{children}</code>;
                                  }
                                  return (
                                    <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.95em]">
                                      {children}
                                    </code>
                                  );
                                },
                                pre: ({ children }) => (
                                  <pre className="my-3 overflow-x-auto rounded-xl bg-black/40 p-4 text-sm">
                                    {children}
                                  </pre>
                                ),
                                h1: ({ children }) => (
                                  <h1 className="mt-4 mb-2 text-lg font-semibold">{children}</h1>
                                ),
                                h2: ({ children }) => (
                                  <h2 className="mt-4 mb-2 text-base font-semibold">{children}</h2>
                                ),
                                h3: ({ children }) => (
                                  <h3 className="mt-3 mb-2 text-sm font-semibold">{children}</h3>
                                ),
                                hr: () => <hr className="my-4 border-white/10" />,
                              }}
                            >
                              {String(msg.content ?? "")}
                            </ReactMarkdown>
                          </div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>

              {isTyping && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex justify-start"
                >
                  <div className="glass-card rounded-2xl px-5 py-4">
                    <div className="flex items-center gap-2">
                      <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
                      <span className="text-sm text-gray-400">Thinking...</span>
                    </div>
                  </div>
                </motion.div>
              )}
            </div>
          </div>

          {/* Input Area */}
          <div className="absolute inset-x-0 bottom-0 z-20 p-4 lg:p-6 border-t border-white/5 bg-black/40 backdrop-blur-md">
            <div className="max-w-3xl mx-auto">
              {!chatEnabled && (
                <div className="mb-3 text-xs text-gray-400">
                  Chat is disabled (set <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono">VITE_CHAT_ENABLED=true</code> to enable).
                </div>
              )}
              {!!sendError && chatEnabled && (
                <div className="mb-3 text-xs text-red-300/90">
                  {sendError}
                </div>
              )}
              <div className="glass-card rounded-2xl p-2 flex items-end gap-2">
                <Textarea
                  ref={textareaRef}
                  placeholder="Ask about your course..."
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={!chatEnabled}
                  className="flex-1 bg-transparent border-0 text-white placeholder:text-gray-500 resize-none min-h-[44px] max-h-[120px] focus-visible:ring-0"
                  rows={1}
                />
                <Button
                  onClick={handleSend}
                  disabled={!chatEnabled || !message.trim() || sendMessageMutation.isPending}
                  className="btn-gradient rounded-xl h-11 w-11 p-0 shrink-0"
                >
                  <Send className="w-4 h-4" />
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
    </div>
  );
}