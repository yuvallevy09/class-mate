import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  FileText,
  MessageSquare,
  Sparkles,
  X,
  ArrowUp,
  Loader2,
  Maximize2,
  Minimize2,
} from "lucide-react";

import { createPageUrl } from "@/utils";
import { getCourseContent, getDownloadUrl } from "@/api/courseContents";
import { getVideoAssetSummary, listVideoAssets, listVideoAssetSegments } from "@/api/videoAssets";
import { listConversationMessages } from "@/api/chat";
import { useAssistantTurn } from "@/hooks/useAssistantTurn";

import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";
import AssistantMessage from "@/components/chat/AssistantMessage";
import ThinkingDisclosure from "@/components/chat/ThinkingDisclosure";
import UserMessage from "@/components/chat/UserMessage";
import VideoConversationSwitcher from "@/components/chat/VideoConversationSwitcher";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Dialog, DialogContent } from "@/components/ui/dialog";

function fmtTimestamp(seconds) {
  const s = Math.max(0, Number(seconds || 0));
  const mm = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

function parseTimestampToSeconds(ts) {
  const t = String(ts || "").trim();
  const m = /^(\d{1,3}):([0-5]\d)$/.exec(t);
  if (!m) return null;
  const mm = Number(m[1]);
  const ss = Number(m[2]);
  if (!Number.isFinite(mm) || !Number.isFinite(ss)) return null;
  return mm * 60 + ss;
}

function summarizeTimestampListToRanges(secondsList) {
  // Keep order, de-dupe.
  const uniq = [];
  const seen = new Set();
  for (const s of secondsList) {
    const n = Number(s);
    if (!Number.isFinite(n)) continue;
    if (seen.has(n)) continue;
    seen.add(n);
    uniq.push(n);
  }
  if (!uniq.length) return [];

  // Group "continuous" timestamps: allow 1–2s steps (matches common transcript chunking).
  const groups = [];
  let start = uniq[0];
  let prev = uniq[0];
  for (let i = 1; i < uniq.length; i += 1) {
    const cur = uniq[i];
    const diff = cur - prev;
    if (diff >= 0 && diff <= 2) {
      prev = cur;
      continue;
    }
    groups.push([start, prev]);
    start = cur;
    prev = cur;
  }
  groups.push([start, prev]);
  return groups;
}

function linkifySummaryTimestamps(text) {
  // Example input: [#0:04, #0:06, #0:15]
  // Output: ([0:04–0:06](videotime:4-6), [0:15](videotime:15))
  const raw = String(text || "");
  const re = /\[(?:#\s*\d{1,3}:[0-5]\d)(?:\s*,\s*#\s*\d{1,3}:[0-5]\d)*\]/g;
  return raw.replace(re, (block) => {
    const tsMatches = Array.from(block.matchAll(/#\s*(\d{1,3}:[0-5]\d)/g)).map((m) => m[1]);
    const secs = tsMatches
      .map(parseTimestampToSeconds)
      .filter((v) => typeof v === "number");
    if (!secs.length) return block;

    const ranges = summarizeTimestampListToRanges(secs);
    const links = ranges.map(([s, e]) => {
      const label = s === e ? fmtTimestamp(s) : `${fmtTimestamp(s)}–${fmtTimestamp(e)}`;
      const href = s === e ? `videotime:${s}` : `videotime:${s}-${e}`;
      return `[${label}](${href})`;
    });
    return `(${links.join(", ")})`;
  });
}

function extractPlainText(node) {
  if (node == null) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractPlainText).join("");
  // React element-ish
  if (typeof node === "object" && "props" in node) return extractPlainText(node.props?.children);
  return "";
}


// Shared height for the video, transcript, chat, and expanded AI Summary cards.
// Sized so the collapsed AI Summary bar (124px, matching the floating toggle
// button stack) spans exactly the same vertical band as those buttons:
// 349px ≈ navbar + breadcrumbs + collapsed summary bar + vertical gaps.
// Never shrinks below the 520px baseline.
const CARD_HEIGHT_CLASS = "lg:h-[max(520px,calc(100vh-349px))]";

// Height of the collapsed AI Summary bar: equal to the floating toggle button
// stack (two h-14 buttons + gap-3), so their tops and bottoms align.
const COLLAPSED_SUMMARY_HEIGHT_CLASS = "lg:h-[124px]";

export default function VideoPlayer() {
  const location = useLocation();
  const { courseId, contentId } = useMemo(() => {
    const urlParams = new URLSearchParams(location.search);
    return {
      courseId: urlParams.get("courseId"),
      contentId: urlParams.get("contentId"),
    };
  }, [location.search]);
  const tParam = useMemo(() => {
    const urlParams = new URLSearchParams(location.search);
    return urlParams.get("t");
  }, [location.search]);

  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState([]);
  // Captured from the first turn's response and reused for the rest of the
  // visit (or set when loading a past conversation via the history switcher).
  // Reset to null on page entry / video switch.
  const [conversationId, setConversationId] = useState(null);
  const [isLoadingConversation, setIsLoadingConversation] = useState(false);

  const queryClient = useQueryClient();
  const [isTranscriptOpen, setIsTranscriptOpen] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isChatPopoutOpen, setIsChatPopoutOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSummaryExpanded, setIsSummaryExpanded] = useState(false);

  const messagesEndRef = useRef(null);
  const chatScrollRef = useRef(null);
  const textareaRef = useRef(null);
  const videoRef = useRef(null);
  const chatWasOpenRef = useRef(false);
  const initialSeekDoneRef = useRef(false);

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

  const {
    data: videoSummary,
    isLoading: isSummaryLoading,
    isFetching: isSummaryFetching,
    error: summaryFetchError,
    refetch: refetchSummary,
  } = useQuery({
    queryKey: ["videoAssetSummary", videoAssetId],
    queryFn: () => getVideoAssetSummary(videoAssetId),
    enabled: !!videoAssetId,
    // Poll while the asset is still processing and no summary has been stored yet.
    // React Query v5: the callback receives the Query object, so read query.state.data.
    refetchInterval: (query) => {
      const data = query?.state?.data;
      const status = String(data?.status || activeVideoAsset?.status || "").toLowerCase();
      const hasSummary = !!String(data?.aiSummary || "").trim();
      const processing = ["uploaded", "processing", "extracting_audio", "transcribing"].includes(status);
      return !hasSummary && processing ? 2000 : false;
    },
  });

  // Viewing context sent with each turn: the watched lecture + the live
  // playback head (read at send time, so it reflects where the student is now).
  const getViewing = () => {
    if (!videoAssetId) return null;
    const raw = videoRef.current?.currentTime;
    const t = Number.isFinite(raw) ? Math.max(0, raw) : null;
    return { watchingVideoAssetId: videoAssetId, watchingTimestampSec: t };
  };

  const handleBeforeSend = ({ text }) => {
    setMessage("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
  };

  const handlePersisted = (info) => {
    const cid = (typeof info?.conversationId === "string" && info.conversationId) || null;
    if (cid) {
      setConversationId(cid);
      // A brand-new thread should show up in the history switcher next open.
      queryClient.invalidateQueries({
        queryKey: ["videoConversations", courseId, videoAssetId],
      });
    }
    // Commit the backend's link-formatted text verbatim (it carries the
    // `#cm-src-N` anchors the citation renderer handles); do NOT use the hook's
    // pill-normalized answer here — that's the course-chat citation format.
    const content = (typeof info?.fullText === "string" && info.fullText) || "";
    const citations = Array.isArray(info?.citations) ? info.citations : [];
    setMessages((prev) => [...prev, { role: "assistant", content, citations }]);
    clearTurn();
  };

  const handleSendError = (err) => {
    const msg = err?.data?.detail || err?.message || "Failed to send message. Please try again.";
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: `Sorry, I encountered an error. ${msg}`, citations: [] },
    ]);
  };

  const { turn, sendMessage, clearTurn, cancel, isPending } = useAssistantTurn({
    courseId,
    conversationId,
    getViewing,
    onBeforeSend: handleBeforeSend,
    onPersisted: handlePersisted,
    onError: handleSendError,
  });

  // Reset per-video transient UI state when navigating between videos: fresh
  // conversation, empty thread, and abort any in-flight turn (so its onDone
  // can't append a stray message to the new video's thread).
  useEffect(() => {
    setMessage("");
    setMessages([]);
    setConversationId(null);
    cancel();
    setIsSummaryExpanded(false);
  }, [courseId, contentId, cancel]);

  // History switcher: load a past conversation for this lecture into the chat.
  const handleSelectConversation = async (cid) => {
    if (!cid || String(cid) === String(conversationId)) return;
    cancel(); // abort any in-flight turn so its onPersisted can't append here
    clearTurn();
    setMessage("");
    setIsLoadingConversation(true);
    try {
      const rows = await queryClient.fetchQuery({
        queryKey: ["conversationMessages", String(cid)],
        queryFn: () => listConversationMessages(cid),
      });
      const mapped = (Array.isArray(rows) ? rows : []).map((m) => ({
        role: m.role,
        content: m.content,
        citations: Array.isArray(m.citations) ? m.citations : [],
        thinking: m.thinking ?? null,
      }));
      setMessages(mapped);
      setConversationId(String(cid));
    } catch (err) {
      setMessages([
        {
          role: "assistant",
          content: `Sorry, I couldn't load that conversation. ${
            err?.data?.detail || err?.message || ""
          }`,
          citations: [],
        },
      ]);
    } finally {
      setIsLoadingConversation(false);
    }
  };

  // History switcher: clear to a fresh thread for this lecture.
  const handleNewConversation = () => {
    cancel();
    clearTurn();
    setMessage("");
    setMessages([]);
    setConversationId(null);
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

  const seekAndScrollToSeconds = (seconds) => {
    const el = videoRef.current;
    if (!el) return;
    try {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch {
      // ignore
    }
    // Let the scroll start, then seek/play (matches the transcript UX feel).
    window.setTimeout(() => seekToSeconds(seconds), 80);
  };

  // If navigated with ?t=SECONDS, seek on first load.
  useEffect(() => {
    if (initialSeekDoneRef.current) return;
    if (!tParam) return;
    const el = videoRef.current;
    if (!el) return;

    let target = Number(tParam);
    if (!Number.isFinite(target)) {
      // Allow mm:ss too.
      const parsed = parseTimestampToSeconds(tParam);
      target = typeof parsed === "number" ? parsed : 0;
    }
    target = Math.max(0, target);

    const doSeek = () => {
      if (initialSeekDoneRef.current) return;
      initialSeekDoneRef.current = true;
      seekToSeconds(target);
    };

    if (el.readyState >= 1) {
      doSeek();
      return;
    }
    el.addEventListener("loadedmetadata", doSeek, { once: true });
    return () => el.removeEventListener("loadedmetadata", doSeek);
  }, [tParam, videoUrl]);

  const aiSummaryText = String(videoSummary?.aiSummary || "").trim();
  const aiSummaryMarkdown = useMemo(() => linkifySummaryTimestamps(aiSummaryText), [aiSummaryText]);
  const aiTitleText = String(videoSummary?.aiTitle || "").trim();

  const markdownComponents = useMemo(
    () => ({
      p: ({ children }) => <p className="my-2 whitespace-pre-wrap">{children}</p>,
      strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
      em: ({ children }) => <em className="italic">{children}</em>,
      ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-1">{children}</ul>,
      ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-1">{children}</ol>,
      li: ({ children }) => <li className="leading-relaxed">{children}</li>,
      a: ({ children, href }) => {
        const h = String(href || "");
        const label = extractPlainText(children).trim();

        if (h.startsWith("#cm-src-")) {
          return (
            <a
              href={h}
              className="ml-0.5 align-super text-[0.75em] text-purple-300 hover:text-purple-200 no-underline"
              onClick={(e) => {
                e.preventDefault();
                scrollChatSourceIntoView(h.slice(1));
              }}
            >
              {children}
            </a>
          );
        }

        // Primary: our synthetic timestamp links.
        if (h.toLowerCase().startsWith("videotime:")) {
          const rest = h.slice("videotime:".length);
          const startStr = rest.split(/[-–]/)[0];
          const start = Number(startStr);
          return (
            <button
              type="button"
              className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
              onClick={() => {
                if (Number.isFinite(start)) seekAndScrollToSeconds(start);
              }}
            >
              {children}
            </button>
          );
        }

        // Fallback: if the link text itself looks like a timestamp or range, treat it as in-page seek.
        // This prevents any navigation even if the model outputs a different href.
        if (/^\d{1,3}:[0-5]\d(–\d{1,3}:[0-5]\d)?$/.test(label)) {
          const startTs = label.split("–")[0];
          const start = parseTimestampToSeconds(startTs);
          return (
            <button
              type="button"
              className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
              onClick={() => {
                if (typeof start === "number") seekAndScrollToSeconds(start);
              }}
            >
              {children}
            </button>
          );
        }

        return (
          <a
            href={h}
            target="_blank"
            rel="noreferrer"
            className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
          >
            {children}
          </a>
        );
      },
      code: ({ className, children }) => {
        const isBlock = String(className || "").includes("language-");
        if (isBlock) return <code className={className}>{children}</code>;
        return (
          <code className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.95em]">
            {children}
          </code>
        );
      },
      pre: ({ children }) => (
        <pre className="my-3 overflow-x-auto rounded-xl bg-black/40 p-4 text-sm">{children}</pre>
      ),
      h1: ({ children }) => <h1 className="mt-4 mb-2 text-lg font-semibold">{children}</h1>,
      h2: ({ children }) => <h2 className="mt-4 mb-2 text-base font-semibold">{children}</h2>,
      h3: ({ children }) => <h3 className="mt-3 mb-2 text-sm font-semibold">{children}</h3>,
      hr: () => <hr className="my-4 border-white/10" />,
    }),
    [seekAndScrollToSeconds]
  );

  const handleSend = () => {
    const userMessage = String(message || "").trim();
    if (!userMessage || isPending || !courseId) return;
    sendMessage(userMessage);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const scrollChatToBottom = (behavior = "smooth") => {
    const el = chatScrollRef.current;
    if (!el) return;
    const top = el.scrollHeight;
    try {
      el.scrollTo({ top, behavior });
    } catch {
      el.scrollTop = top;
    }
  };

  const scrollChatSourceIntoView = (sourceId) => {
    const container = chatScrollRef.current;
    const target = typeof document !== "undefined" ? document.getElementById(sourceId) : null;
    if (!container || !target) return;

    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const top =
      targetRect.top - containerRect.top + container.scrollTop - container.clientHeight / 2 + targetRect.height / 2;

    try {
      container.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    } catch {
      container.scrollTop = Math.max(0, top);
    }
  };

  const openChatPopout = () => {
    chatWasOpenRef.current = !!isChatOpen;
    setIsChatPopoutOpen(true);
    setIsChatOpen(false);
  };

  const closeChatPopout = () => {
    setIsChatPopoutOpen(false);
    if (chatWasOpenRef.current) setIsChatOpen(true);
  };

  useEffect(() => {
    window.requestAnimationFrame(() => scrollChatToBottom("smooth"));
  }, [messages.length, turn]);

  // Assistant body, shared by committed messages and the live turn. Uses the
  // course-chat renderer (inline citation pills + popover) for visual parity;
  // `onSeek`/`currentContentId` make timestamps for the on-screen lecture seek
  // the in-page player instead of opening a new tab.
  const renderAssistantBody = (content, citations) => (
    <AssistantMessage
      content={content}
      citations={citations}
      onSeek={seekAndScrollToSeconds}
      currentContentId={contentId}
    />
  );

  const renderChatPanel = ({ scrollHeightClass, variant }) => {
    const isPopout = variant === "popout";
    return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      className="glass-card rounded-2xl overflow-hidden"
    >
      <div className={`relative flex flex-col ${isPopout ? "" : CARD_HEIGHT_CLASS}`}>
      <div className="p-4 border-b border-white/5 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-purple-400" />
          <h2 className="text-lg font-semibold">Chat</h2>
        </div>
        <div className="flex items-center gap-1">
          <VideoConversationSwitcher
            courseId={courseId}
            videoAssetId={videoAssetId}
            activeConversationId={conversationId}
            onSelect={handleSelectConversation}
            onNew={handleNewConversation}
          />
          {isPopout ? (
            <Button
              variant="ghost"
              size="icon"
              onClick={closeChatPopout}
              className="text-gray-400 hover:text-white hover:bg-white/5"
              aria-label="Minimize chat"
            >
              <Minimize2 className="w-5 h-5" />
            </Button>
          ) : (
            <>
              <Button
                variant="ghost"
                size="icon"
                onClick={openChatPopout}
                className="text-gray-400 hover:text-white hover:bg-white/5"
                aria-label="Pop out chat"
              >
                <Maximize2 className="w-5 h-5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setIsChatOpen(false)}
                className="text-gray-400 hover:text-white hover:bg-white/5"
                aria-label="Close chat"
              >
                <X className="w-5 h-5" />
              </Button>
            </>
          )}
        </div>
      </div>

      <div ref={chatScrollRef} className={`${scrollHeightClass} overflow-y-auto px-4 pt-4 pb-24`}>
        <div className="space-y-4">
          {isLoadingConversation && (
            <div className="text-center py-8">
              <Loader2 className="w-8 h-8 text-purple-400 mx-auto animate-spin" />
            </div>
          )}
          {!isLoadingConversation && messages.length === 0 && !turn && (
            <div className="text-center py-8">
              <Sparkles className="w-10 h-10 text-purple-400 mx-auto mb-2" />
              <p className="text-gray-400 text-sm">Ask questions about the lecture</p>
            </div>
          )}
          {messages.map((msg, index) => {
            return (
              <motion.div
                key={`${msg.role}-${index}`}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {msg.role === "user" ? (
                  <UserMessage content={msg.content} />
                ) : (
                  <div className="w-full">
                    {msg.thinking ? <ThinkingDisclosure thinkingText={msg.thinking} /> : null}
                    {renderAssistantBody(msg.content, msg.citations)}
                  </div>
                )}
              </motion.div>
            );
          })}
          {turn && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="flex justify-start">
              <div className="w-full">
                <ThinkingDisclosure
                  phase={turn.phase}
                  stage={turn.stage}
                  statusLabel={turn.statusLabel}
                  thinkingText={turn.thinkingText}
                  thoughtForSecs={turn.thoughtForSecs}
                />
                {turn.answer ? renderAssistantBody(turn.answer, turn.citations) : null}
              </div>
            </motion.div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Floating input bar (mirrors CourseChat): centered pill over the
          messages rather than a full-width docked section. */}
      <div className="absolute inset-x-0 bottom-4 z-20 px-4 pointer-events-none">
        <div className="w-full max-w-[560px] mx-auto pointer-events-auto">
          <div className="flex items-end gap-2.5 rounded-[22px] border border-white/10 bg-[#16151C]/80 py-1.5 pl-[18px] pr-1.5 backdrop-blur-xl shadow-[0_16px_40px_rgba(0,0,0,0.5),0_0_0_1px_rgba(0,0,0,0.25)]">
            <textarea
              ref={textareaRef}
              placeholder="Ask a question..."
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={!courseId || isPending}
              className="flex-1 bg-transparent border-0 px-0 py-2 leading-6 text-white placeholder:text-gray-500 resize-none min-h-[40px] max-h-[120px] outline-none ring-0 focus:outline-none focus:ring-0 focus:bg-transparent focus-visible:outline-none focus-visible:ring-0 focus-visible:bg-transparent"
              rows={1}
            />
            <Button
              onClick={handleSend}
              disabled={!String(message || "").trim() || isPending}
              className="btn-gradient rounded-full h-9 w-9 p-0 shrink-0 mb-[3px]"
            >
              <ArrowUp className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>
      </div>
    </motion.div>
    );
  };

  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 right-0 w-[600px] h-[600px] bg-purple-500/5 rounded-full blur-[200px]" />
        <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-pink-500/5 rounded-full blur-[200px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen((v) => !v)} showMenu={true} />

      <div className="flex-1 flex flex-col relative z-10 w-full">
        {/* Breadcrumbs (shared across main + sidebar so sidebar aligns with video) */}
        <div className="w-full max-w-7xl mx-auto px-6 lg:px-8 pt-6 lg:pt-8">
          <div>
            <div className="mb-6 flex items-center gap-2 text-sm">
              <Link
                to={createPageUrl(`CourseContent?courseId=${courseId}&category=media`)}
                className="text-gray-400 hover:text-white transition-colors"
              >
                Videos
              </Link>
              <span className="text-gray-600">/</span>
              <span className="text-white font-medium">{aiTitleText || content?.title || "Video"}</span>
            </div>
          </div>
        </div>

        {/* 3-column grid on lg+: [gutter | centered content | gutter]. The video
            column is always the center column, so opening the transcript/chat
            widgets (which live in the right gutter) never moves the video. */}
        <div
          className={`flex flex-1 overflow-hidden lg:grid ${
            isTranscriptOpen || isChatOpen
              ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1280px)_minmax(280px,1fr)]"
              : "lg:grid-cols-[minmax(0,1fr)_minmax(0,1280px)_minmax(0,1fr)]"
          }`}
        >
          {/* Toggle Buttons */}
          <div className="fixed bottom-6 right-6 flex flex-col gap-3 z-50">
            {!isTranscriptOpen && (
              <Button
                onClick={() => setIsTranscriptOpen(true)}
                className="btn-gradient rounded-full h-14 w-14 p-0 shadow-lg"
              >
                <FileText className="w-6 h-6" />
              </Button>
            )}
            {!isChatOpen && (
              <Button
                onClick={() => setIsChatOpen(true)}
                className="btn-gradient rounded-full h-14 w-14 p-0 shadow-lg"
              >
                <MessageSquare className="w-6 h-6" />
              </Button>
            )}
          </div>

          {/* Main Content */}
          <div className="flex-1 flex flex-col min-w-0 lg:col-start-2 px-6 lg:px-8 overflow-y-auto">
            <div className="glass-card rounded-2xl overflow-hidden mb-6">
              <div className={`relative w-full bg-black aspect-video lg:aspect-auto ${CARD_HEIGHT_CLASS}`}>
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

            {/* AI Summary */}
            <div
              className={`glass-card rounded-2xl p-6 mb-6 flex flex-col ${
                isSummaryExpanded ? CARD_HEIGHT_CLASS : COLLAPSED_SUMMARY_HEIGHT_CLASS
              }`}
            >
              <button
                onClick={() => setIsSummaryExpanded((v) => !v)}
                className="flex items-center justify-between w-full mb-4 hover:opacity-80 transition-opacity shrink-0"
              >
                <div className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-purple-400" />
                  <h2 className="text-xl font-semibold">ClassMate's Notes</h2>
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
                    className="flex flex-col lg:flex-1 lg:min-h-0"
                  >
                    {isSummaryLoading || isSummaryFetching ? (
                      <div className="text-center py-12">
                        <Loader2 className="w-12 h-12 text-purple-400 mx-auto mb-3 animate-spin" />
                        <p className="text-gray-400">Loading summary...</p>
                      </div>
                    ) : summaryFetchError ? (
                      <div className="text-center py-12">
                        <Sparkles className="w-12 h-12 text-gray-500 mx-auto mb-3" />
                        <p className="text-gray-500">Couldn’t load the stored summary.</p>
                        <Button
                          type="button"
                          variant="ghost"
                          className="mt-3 border border-white/10 hover:bg-white/5 text-gray-200"
                          onClick={() => refetchSummary()}
                        >
                          Try again
                        </Button>
                      </div>
                    ) : videoSummary?.aiSummaryError ? (
                      <div className="text-center py-12">
                        <Sparkles className="w-12 h-12 text-gray-500 mx-auto mb-3" />
                        <p className="text-gray-500">{String(videoSummary.aiSummaryError)}</p>
                        <Button
                          type="button"
                          variant="ghost"
                          className="mt-3 border border-white/10 hover:bg-white/5 text-gray-200"
                          onClick={() => refetchSummary()}
                        >
                          Refresh
                        </Button>
                      </div>
                    ) : aiSummaryText ? (
                      <div className="text-gray-300 text-sm leading-relaxed max-h-[400px] lg:max-h-none lg:flex-1 lg:min-h-0 overflow-y-auto pr-2">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={markdownComponents}
                        >
                          {String(aiSummaryMarkdown || "")}
                        </ReactMarkdown>
                      </div>
                    ) : activeVideoAsset?.status &&
                      ["uploaded", "processing", "extracting_audio", "transcribing"].includes(
                        String(activeVideoAsset.status || "").toLowerCase()
                      ) ? (
                      <div className="text-center py-12">
                        <Loader2 className="w-12 h-12 text-purple-400 mx-auto mb-3 animate-spin" />
                        <p className="text-gray-400">Summary will appear when processing completes…</p>
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

          {/* Right Sidebar - Transcript & Chat */}
          <AnimatePresence>
            {(isTranscriptOpen || isChatOpen) && (
              <motion.div
                initial={{ x: "100%" }}
                animate={{ x: 0 }}
                exit={{ x: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 300 }}
                className="w-full lg:col-start-3 lg:w-auto lg:max-w-[428px] lg:min-w-0 flex flex-col gap-6 px-4 lg:-ml-8 lg:px-7 pb-4 pt-0"
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
                      <div className={`flex flex-col ${CARD_HEIGHT_CLASS}`}>
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
                      <ScrollArea className="h-[455px] lg:h-auto lg:flex-1 min-h-0 px-4 py-2">
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
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Chat */}
                <AnimatePresence>
                  {isChatOpen && (
                    renderChatPanel({
                      scrollHeightClass: "h-[300px] lg:h-auto lg:flex-1 lg:min-h-0",
                      variant: "sidebar",
                    })
                  )}
                </AnimatePresence>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

      </div>

      <CourseSidebar
        courseId={courseId}
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        activeCategory="media"
      />

      <Dialog
        open={isChatPopoutOpen}
        onOpenChange={(open) => {
          setIsChatPopoutOpen(open);
          if (!open && chatWasOpenRef.current) setIsChatOpen(true);
        }}
      >
        <DialogContent
          showClose={false}
          className="border-0 bg-transparent p-0 shadow-none w-[min(960px,calc(100vw-2rem))] max-w-none"
        >
          {renderChatPanel({ scrollHeightClass: "h-[60vh]", variant: "popout" })}
        </DialogContent>
      </Dialog>
    </div>
  );
}

