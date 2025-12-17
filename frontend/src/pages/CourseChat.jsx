import React, { useState, useRef, useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { client } from "@/api/client";
import { listConversationMessages, sendCourseChat } from "@/api/chat";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Sparkles, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
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
  const messagesEndRef = useRef(null);
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

  const sendMessageMutation = useMutation({
    mutationFn: async (userMessage) => {
      const response = await sendCourseChat({
        courseId,
        message: userMessage,
        conversationId: activeConversationId,
      });

      const newConversationId =
        (typeof response?.conversationId === "string" && response.conversationId) ||
        (typeof response?.conversation_id === "string" && response.conversation_id) ||
        null;

      // If this started a new conversation, reflect it in the URL so refresh/share works.
      if (!activeConversationId && newConversationId) {
        navigate(createPageUrl(`CourseChat?id=${courseId}&conversationId=${encodeURIComponent(newConversationId)}`), {
          replace: true,
        });
      }
    },
    onMutate: () => {
      setIsTyping(true);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations', courseId] });
      queryClient.invalidateQueries({ queryKey: ['conversationMessages', conversationId] });
      setMessage("");
      setIsTyping(false);
    },
    onError: () => {
      setIsTyping(false);
    }
  });

  const handleSend = () => {
    if (!chatEnabled) return;
    if (message.trim() && !sendMessageMutation.isPending) {
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
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  return (
    <div className="min-h-screen flex flex-col relative">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 right-0 w-[600px] h-[600px] bg-purple-500/5 rounded-full blur-[200px]" />
        <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-pink-500/5 rounded-full blur-[200px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen(!isSidebarOpen)} showMenu={true} />

      {/* Main Content */}
      <div className="flex-1 flex relative z-10 overflow-hidden">
        {/* Chat Area */}
        <div className="flex-1 flex flex-col">
          <ScrollArea className="flex-1 px-4 lg:px-8 py-6">
            <div className="max-w-3xl mx-auto space-y-6">
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
                {messages.map((msg, index) => (
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
                        <p className="text-sm lg:text-base leading-relaxed whitespace-pre-wrap">
                          {msg.content}
                        </p>
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

              <div ref={messagesEndRef} />
            </div>
          </ScrollArea>

          {/* Input Area */}
          <div className="p-4 lg:p-6 border-t border-white/5">
            <div className="max-w-3xl mx-auto">
              {!chatEnabled && (
                <div className="mb-3 text-xs text-gray-400">
                  Chat is disabled until the LLM/chat endpoint is implemented.
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