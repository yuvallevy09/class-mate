import React, { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { createPageUrl } from "@/utils";
import { client } from "@/api/client";
import { listCourseContents } from "@/api/courseContents";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { 
  X, Send, BookOpen, FileText, 
  ClipboardList, FolderOpen, Sparkles, Loader2,
  Image, FileQuestion, PenTool, MessageSquarePlus, History, ChevronRight
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import Navbar from "@/components/Navbar";

const SIDEBAR_ITEMS = [
  { id: "overview", label: "Overview", icon: BookOpen, type: "content" },
  { id: "media", label: "Course Media", icon: Image, type: "content" },
  { id: "notes", label: "Notes", icon: PenTool, type: "content" },
  { id: "past_exams", label: "Past Exams", icon: FileQuestion, type: "content" },
  { id: "past_assignments", label: "Past Assignments", icon: ClipboardList, type: "content" },
  { id: "additional_resources", label: "Additional Resources", icon: FolderOpen, type: "content" },
  { id: "general", label: "General", icon: FileText, type: "content" }
];

export default function CourseChat() {
  const urlParams = new URLSearchParams(window.location.search);
  const courseId = urlParams.get("id");
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

  const { data: course } = useQuery({
    queryKey: ['course', courseId],
    queryFn: async () => {
      const courses = await client.entities.Course.filter({ id: courseId });
      return courses[0];
    },
    enabled: !!courseId
  });

  const { data: messages = [] } = useQuery({
    queryKey: ['messages', courseId],
    queryFn: () => client.entities.ChatMessage.filter({ course_id: courseId }, 'created_date'),
    enabled: !!courseId
  });

  const { data: courseContent = [] } = useQuery({
    queryKey: ['courseContent', courseId],
    queryFn: () => listCourseContents(courseId),
    enabled: !!courseId
  });

  const sendMessageMutation = useMutation({
    mutationFn: async (userMessage) => {
      // Save user message
      await client.entities.ChatMessage.create({
        course_id: courseId,
        role: "user",
        content: userMessage
      });

      // Get assistant response (v0: backend stub, later: real LLM + citations).
      const response = await client.integrations.Core.InvokeLLM({
        courseId,
        message: userMessage,
        conversationId: null,
      });

      // Save AI response
      const assistantText = typeof response?.text === "string" ? response.text : String(response ?? "");
      await client.entities.ChatMessage.create({
        course_id: courseId,
        role: "assistant",
        content: assistantText
      });
    },
    onMutate: () => {
      setIsTyping(true);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', courseId] });
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

        {/* Sidebar */}
        <AnimatePresence>
          {isSidebarOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 bg-black/50 z-30 lg:hidden"
                onClick={() => setIsSidebarOpen(false)}
              />
              <motion.aside
                initial={{ x: '100%' }}
                animate={{ x: 0 }}
                exit={{ x: '100%' }}
                transition={{ type: "spring", damping: 25, stiffness: 300 }}
                className="fixed right-0 top-[73px] bottom-0 w-80 glass-card border-l border-white/5 z-40 lg:relative lg:top-0 lg:z-10"
              >
                <div className="p-6 border-b border-white/5 flex items-center justify-between">
                  <Link to={createPageUrl("Courses")} onClick={() => setIsSidebarOpen(false)}>
                    <Button variant="ghost" className="text-gray-400 hover:text-white hover:bg-white/5 px-3">
                      <ChevronRight className="w-4 h-4 mr-2 rotate-180" />
                      My Courses
                    </Button>
                  </Link>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => setIsSidebarOpen(false)}
                    className="text-gray-400 hover:text-white hover:bg-white/5 lg:hidden"
                  >
                    <X className="w-5 h-5" />
                  </Button>
                </div>
                <ScrollArea className="h-[calc(100%-73px)]">
                  <div className="p-4 space-y-6">
                    {/* Chat Section */}
                    <div>
                      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 px-2">
                        Chat
                      </h3>
                      <div className="space-y-2">
                        <Link
                          to={createPageUrl(`CourseChat?id=${courseId}`)}
                          onClick={() => {
                            setIsSidebarOpen(false);
                            window.location.reload();
                          }}
                        >
                          <motion.button
                            whileHover={{ x: 4 }}
                            className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left text-gray-300 hover:text-white hover:bg-white/5 transition-colors"
                          >
                            <MessageSquarePlus className="w-5 h-5 text-purple-400" />
                            <span className="text-sm font-medium">New Chat</span>
                          </motion.button>
                        </Link>
                        <motion.button
                          whileHover={{ x: 4 }}
                          className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left text-gray-300 hover:text-white hover:bg-white/5 transition-colors"
                        >
                          <History className="w-5 h-5 text-purple-400" />
                          <span className="text-sm font-medium">Past Conversations</span>
                          <span className="ml-auto text-xs text-gray-500">Coming Soon</span>
                        </motion.button>
                      </div>
                    </div>

                    {/* Course Content Section */}
                    <div>
                      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 px-2">
                        Course Content
                      </h3>
                      <div className="space-y-2">
                        {SIDEBAR_ITEMS.map((item) => (
                          <Link
                            key={item.id}
                            to={createPageUrl(`CourseContent?courseId=${courseId}&category=${item.id}`)}
                          >
                            <motion.button
                              whileHover={{ x: 4 }}
                              className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left text-gray-300 hover:text-white hover:bg-white/5 transition-colors"
                            >
                              <item.icon className="w-5 h-5 text-purple-400" />
                              <span className="text-sm font-medium">{item.label}</span>
                            </motion.button>
                          </Link>
                        ))}
                      </div>
                    </div>
                  </div>
                </ScrollArea>
              </motion.aside>
            </>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}