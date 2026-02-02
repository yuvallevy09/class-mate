import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  MessageSquare,
  Sparkles,
  X,
  Edit3,
  Send,
  Loader2,
} from "lucide-react";

import { createPageUrl } from "@/utils";
import { getCourse } from "@/api/courses";
import { getCourseContent, getDownloadUrl } from "@/api/courseContents";
import { listVideoAssets, listVideoAssetSegments } from "@/api/videoAssets";
import { sendVideoChat } from "@/api/chat";

import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "@/components/ui/use-toast";

function fmtTimestamp(seconds) {
  const s = Math.max(0, Number(seconds || 0));
  const mm = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

export default function VideoPlayer() {
  const urlParams = new URLSearchParams(window.location.search);
  const courseId = urlParams.get("courseId");
  const contentId = urlParams.get("contentId");

  const [message, setMessage] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [messages, setMessages] = useState([]);
  const [note, setNote] = useState("");
  const [isTranscriptOpen, setIsTranscriptOpen] = useState(true);
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isGeneratingSummary, setIsGeneratingSummary] = useState(false);
  const [aiSummary, setAiSummary] = useState("");
  const [isSummaryExpanded, setIsSummaryExpanded] = useState(true);

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const videoRef = useRef(null);

  const noteStorageKey = useMemo(() => {
    if (!courseId || !contentId) return null;
    return `classmate:note:${courseId}:${contentId}`;
  }, [courseId, contentId]);

  useEffect(() => {
    if (!noteStorageKey) return;
    try {
      const saved = window.localStorage.getItem(noteStorageKey);
      if (typeof saved === "string") setNote(saved);
    } catch {
      // ignore
    }
  }, [noteStorageKey]);

  const { data: course } = useQuery({
    queryKey: ["course", courseId],
    queryFn: () => getCourse(courseId),
    enabled: !!courseId,
  });

  const { data: content } = useQuery({
    queryKey: ["contentById", contentId],
    queryFn: () => getCourseContent(contentId),
    enabled: !!contentId,
  });

  const { data: download } = useQuery({
    queryKey: ["contentDownloadUrl", contentId],
    queryFn: () => getDownloadUrl(contentId),
    enabled: !!contentId,
  });

  const videoUrl = download?.url || null;

  const { data: videoAssets = [] } = useQuery({
    queryKey: ["videoAssets", courseId],
    queryFn: () => listVideoAssets(courseId),
    enabled: !!courseId,
  });

  const activeVideoAsset = useMemo(() => {
    if (!contentId || !Array.isArray(videoAssets)) return null;
    return videoAssets.find((a) => String(a?.content_id) === String(contentId)) || null;
  }, [videoAssets, contentId]);

  const videoAssetId = activeVideoAsset?.id || null;

  const { data: transcriptSegments = [] } = useQuery({
    queryKey: ["videoAssetSegments", videoAssetId],
    queryFn: () => listVideoAssetSegments(videoAssetId),
    enabled: !!videoAssetId,
  });

  const handleSaveNote = () => {
    if (!noteStorageKey) return;
    try {
      window.localStorage.setItem(noteStorageKey, String(note || ""));
      toast({ title: "Saved", description: "Your note was saved in this browser." });
    } catch (e) {
      toast({
        title: "Couldn’t save note",
        description: "Your browser blocked local storage. Try a different browser setting.",
      });
    }
  };

  const seekToSeconds = (seconds) => {
    const el = videoRef.current;
    if (!el) return;
    const t = Math.max(0, Number(seconds || 0));
    try {
      el.currentTime = t;
      el.play?.();
    } catch {
      // ignore
    }
  };

  const handleSend = async () => {
    const userMessage = String(message || "").trim();
    if (!userMessage || isTyping || !courseId) return;

    const history = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    setMessage("");
    setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    setIsTyping(true);

    try {
      const res = await sendVideoChat({
        courseId,
        mode: "chat",
        message: userMessage,
        history,
        contentId,
        videoAssetId,
      });
      setMessages((prev) => [...prev, { role: "assistant", content: res?.text || "" }]);
    } catch (e) {
      const msg = e?.data?.detail || e?.message || "Failed to send message. Please try again.";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Sorry, I encountered an error. ${msg}` },
      ]);
    } finally {
      setIsTyping(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, isTyping]);

  useEffect(() => {
    if (!contentId || !courseId) return;
    if (aiSummary || isGeneratingSummary) return;
    setIsGeneratingSummary(true);
    sendVideoChat({
      courseId,
      mode: "summary",
      message: "",
      history: [],
      contentId,
      videoAssetId,
    })
      .then((res) => setAiSummary(String(res?.text || "").trim()))
      .catch(() => {
        // best-effort
      })
      .finally(() => setIsGeneratingSummary(false));
  }, [contentId, courseId, videoAssetId, aiSummary, isGeneratingSummary]);

  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 right-0 w-[600px] h-[600px] bg-purple-500/5 rounded-full blur-[200px]" />
        <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-pink-500/5 rounded-full blur-[200px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen((v) => !v)} showMenu={true} />

      <div className="flex-1 flex flex-col relative z-10">
        <div className="flex flex-1 overflow-hidden">
          {/* Toggle Buttons */}
          <div className="fixed bottom-6 right-6 flex flex-col gap-3 z-50">
            {!isChatOpen && (
              <Button
                onClick={() => setIsChatOpen(true)}
                className="btn-gradient rounded-full h-14 w-14 p-0 shadow-lg"
              >
                <MessageSquare className="w-6 h-6" />
              </Button>
            )}
            {!isTranscriptOpen && (
              <Button
                onClick={() => setIsTranscriptOpen(true)}
                className="btn-gradient rounded-full h-14 w-14 p-0 shadow-lg"
              >
                <FileText className="w-6 h-6" />
              </Button>
            )}
          </div>

          {/* Main Content */}
          <div className="flex-1 flex flex-col p-6 lg:p-8 overflow-y-auto max-w-6xl">
            <div className="mb-6 flex items-center gap-2 text-sm">
              <Link
                to={createPageUrl(`CourseContent?courseId=${courseId}&category=media`)}
                className="text-gray-400 hover:text-white transition-colors"
              >
                Videos
              </Link>
              <span className="text-gray-600">/</span>
              <span className="text-white font-medium">{content?.title || "Video"}</span>
            </div>

            <div className="glass-card rounded-2xl overflow-hidden mb-6">
              <div className="relative w-full aspect-video bg-black">
                <video
                  ref={videoRef}
                  key={videoUrl || "video"}
                  controls
                  playsInline
                  className="w-full h-full"
                  src={videoUrl || undefined}
                >
                  Your browser does not support the video tag.
                </video>
              </div>
            </div>

            {/* My Notes */}
            <div className="glass-card rounded-2xl p-6 flex flex-col h-[422px]">
              <div className="flex items-center gap-2 mb-4 shrink-0">
                <Edit3 className="w-5 h-5 text-purple-400" />
                <h2 className="text-xl font-semibold">My Notes</h2>
              </div>

              <Textarea
                placeholder="Write your notes here..."
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="flex-1 bg-white/5 border-white/10 text-white placeholder:text-gray-500 resize-none"
              />

              <Button onClick={handleSaveNote} className="btn-gradient mt-4 w-full">
                Save Note
              </Button>
            </div>
          </div>

          {/* Right Sidebar - Transcript & Chat */}
          <AnimatePresence>
            {(isTranscriptOpen || isChatOpen) && (
              <motion.div
                initial={{ x: "100%" }}
                animate={{ x: 0 }}
                exit={{ x: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 300 }}
                className="w-full lg:w-[380px] flex flex-col gap-4 p-4"
              >
                {/* Transcript */}
                <AnimatePresence>
                  {isTranscriptOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="glass-card rounded-2xl overflow-hidden"
                    >
                      <div className="p-4 border-b border-white/5 flex items-center justify-between shrink-0">
                        <div className="flex items-center gap-2">
                          <FileText className="w-5 h-5 text-purple-400" />
                          <h2 className="text-lg font-semibold">Transcript</h2>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setIsTranscriptOpen(false)}
                          className="text-gray-400 hover:text-white hover:bg-white/5"
                        >
                          <X className="w-5 h-5" />
                        </Button>
                      </div>
                      <ScrollArea className="h-[300px] px-4 py-2">
                        {Array.isArray(transcriptSegments) && transcriptSegments.length ? (
                          <div className="space-y-2">
                            {transcriptSegments.map((seg) => (
                              <button
                                key={seg.id}
                                onClick={() => seekToSeconds(seg.start_sec)}
                                className="w-full text-left p-2 rounded-lg hover:bg-white/5 transition-colors group"
                              >
                                <span className="text-xs text-purple-400 group-hover:text-purple-300">
                                  {fmtTimestamp(seg.start_sec)}
                                </span>
                                <p className="text-sm text-gray-300 mt-1">{seg.text}</p>
                              </button>
                            ))}
                          </div>
                        ) : (
                          <div className="text-center py-12">
                            <FileText className="w-12 h-12 text-gray-500 mx-auto mb-3" />
                            <p className="text-gray-500 text-sm">No transcript available</p>
                          </div>
                        )}
                      </ScrollArea>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Chat */}
                <AnimatePresence>
                  {isChatOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="flex flex-col glass-card rounded-2xl overflow-hidden"
                    >
                      <div className="p-4 border-b border-white/5 flex items-center justify-between shrink-0">
                        <div className="flex items-center gap-2">
                          <MessageSquare className="w-5 h-5 text-purple-400" />
                          <h2 className="text-lg font-semibold">Chat</h2>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setIsChatOpen(false)}
                          className="text-gray-400 hover:text-white hover:bg-white/5"
                        >
                          <X className="w-5 h-5" />
                        </Button>
                      </div>

                      <ScrollArea className="h-[300px] px-4 py-4">
                        <div className="space-y-4">
                          {messages.length === 0 && !isTyping && (
                            <div className="text-center py-8">
                              <Sparkles className="w-10 h-10 text-purple-400 mx-auto mb-2" />
                              <p className="text-gray-400 text-sm">Ask questions about the lecture</p>
                            </div>
                          )}
                          {messages.map((msg, index) => (
                            <motion.div
                              key={`${msg.role}-${index}`}
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                            >
                              <div className="max-w-[85%]">
                                <div
                                  className={`rounded-2xl px-4 py-2 ${
                                    msg.role === "user"
                                      ? "bg-gradient-to-r from-pink-500 via-purple-500 to-blue-500 text-white"
                                      : "glass-card text-gray-100"
                                  }`}
                                >
                                  <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                                </div>
                              </div>
                            </motion.div>
                          ))}
                          {isTyping && (
                            <motion.div
                              initial={{ opacity: 0 }}
                              animate={{ opacity: 1 }}
                              className="flex justify-start"
                            >
                              <div className="glass-card rounded-2xl px-4 py-2">
                                <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
                              </div>
                            </motion.div>
                          )}
                          <div ref={messagesEndRef} />
                        </div>
                      </ScrollArea>

                      <div className="p-4 border-t border-white/5 shrink-0">
                        <div className="glass-card rounded-xl p-2 flex items-end gap-2">
                          <Textarea
                            ref={textareaRef}
                            placeholder="Ask a question..."
                            value={message}
                            onChange={(e) => setMessage(e.target.value)}
                            onKeyDown={handleKeyDown}
                            disabled={!courseId || isTyping}
                            className="flex-1 bg-transparent border-0 text-white placeholder:text-gray-500 resize-none min-h-[44px] max-h-[120px] focus-visible:ring-0"
                            rows={1}
                          />
                          <Button
                            onClick={handleSend}
                            disabled={!String(message || "").trim() || isTyping}
                            className="btn-gradient rounded-xl h-10 w-10 p-0 shrink-0"
                          >
                            <Send className="w-4 h-4" />
                          </Button>
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* AI Summary - Full Width Bottom */}
        <div className="border-t border-white/5 p-6 lg:p-8">
          <div className="max-w-7xl mx-auto">
            <div className="glass-card rounded-2xl p-6">
              <button
                onClick={() => setIsSummaryExpanded((v) => !v)}
                className="flex items-center justify-between w-full mb-4 hover:opacity-80 transition-opacity"
              >
                <div className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-purple-400" />
                  <h2 className="text-xl font-semibold">AI Summary</h2>
                </div>
                <span className={`text-gray-400 transition-transform ${isSummaryExpanded ? "" : "-rotate-90"}`}>
                  ▾
                </span>
              </button>

              <AnimatePresence>
                {isSummaryExpanded && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                  >
                    {isGeneratingSummary ? (
                      <div className="text-center py-12">
                        <Loader2 className="w-12 h-12 text-purple-400 mx-auto mb-3 animate-spin" />
                        <p className="text-gray-400">Generating summary...</p>
                      </div>
                    ) : aiSummary ? (
                      <div className="text-gray-300 text-sm leading-relaxed whitespace-pre-wrap max-h-[400px] overflow-y-auto pr-2">
                        {aiSummary}
                      </div>
                    ) : (
                      <div className="text-center py-12">
                        <Sparkles className="w-12 h-12 text-gray-500 mx-auto mb-3" />
                        <p className="text-gray-500">Summary will appear here</p>
                      </div>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>
      </div>

      <CourseSidebar
        courseId={courseId}
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        activeCategory="media"
      />
    </div>
  );
}

