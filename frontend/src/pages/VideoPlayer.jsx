import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  FileText,
  MessageSquare,
  Sparkles,
  X,
  Send,
  Loader2,
  Maximize2,
  Minimize2,
} from "lucide-react";

import { createPageUrl } from "@/utils";
import { getCourseContent, getDownloadUrl } from "@/api/courseContents";
import { getVideoAssetSummary, listVideoAssets, listVideoAssetSegments } from "@/api/videoAssets";
import { sendVideoChat } from "@/api/chat";

import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";
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

function isVideoCitation(citation) {
  const extra = citation?.extra || {};
  const t = String(extra?.type || "").toLowerCase();
  const citedContentId = citation?.content_id || citation?.contentId || null;
  return t === "video" && !!citedContentId;
}

function getCitationContentId(citation) {
  return citation?.content_id || citation?.contentId || null;
}

function getCitationTitle(citation) {
  const extra = citation?.extra || {};
  return (
    String(citation?.title || "").trim() ||
    String(extra?.original_filename || "").trim() ||
    String(getCitationContentId(citation) || "").trim() ||
    "Course content"
  );
}

function buildSourcesModel(citations = []) {
  const items = Array.isArray(citations) ? citations : [];
  const videoGroups = new Map();
  const nonVideo = [];

  items.forEach((citation, index) => {
    const i = index + 1;
    if (!isVideoCitation(citation)) {
      nonVideo.push({ i, citation });
      return;
    }

    const contentId = String(getCitationContentId(citation));
    const extra = citation?.extra || {};
    const start = Number(extra?.startSec || 0);
    const end = Number(extra?.endSec || start);
    const sInt = Math.max(0, Math.floor(start));
    const eInt = Math.max(0, Math.floor(end));
    const rangeKey = `${sInt}-${eInt}`;
    const chapterId = extra?.chapterId ?? extra?.chapter_id ?? null;

    if (!videoGroups.has(contentId)) {
      videoGroups.set(contentId, {
        contentId,
        leaderIndex: i,
        title: getCitationTitle(citation),
        ranges: new Map(),
      });
    }

    const group = videoGroups.get(contentId);
    group.leaderIndex = Math.min(group.leaderIndex, i);
    if (!group.title) group.title = getCitationTitle(citation);

    if (!group.ranges.has(rangeKey)) {
      group.ranges.set(rangeKey, {
        s: sInt,
        e: eInt,
        url: typeof citation?.url === "string" ? citation.url : null,
        chapterId: chapterId ? String(chapterId) : null,
        chapterTitle:
          (typeof extra?.chapterTitle === "string" && extra.chapterTitle.trim()) || null,
        citationIndices: [],
      });
    }

    const range = group.ranges.get(rangeKey);
    if (!range.url && typeof citation?.url === "string") range.url = citation.url;
    if (!range.chapterId && chapterId) range.chapterId = String(chapterId);
    if (!range.chapterTitle && typeof extra?.chapterTitle === "string" && extra.chapterTitle.trim()) {
      range.chapterTitle = extra.chapterTitle.trim();
    }
    range.citationIndices.push(i);
  });

  const video = Array.from(videoGroups.values())
    .sort((a, b) => a.leaderIndex - b.leaderIndex)
    .map((group) => {
      const ranges = Array.from(group.ranges.values());
      return {
        ...group,
        ranges: ranges.map((range, index) => ({
          ...range,
          letter: String.fromCharCode("a".charCodeAt(0) + Math.min(index, 25)),
        })),
      };
    });

  return { video, nonVideo };
}

function getSourceAnchorId(messageKey, citationIndex) {
  return `cm-src-${messageKey}-${citationIndex}`;
}

function rewriteCitationAnchors(text, messageKey) {
  return String(text || "").replace(/#cm-src-(\d+)/g, (_match, rawIndex) => {
    return `#${getSourceAnchorId(messageKey, Number(rawIndex))}`;
  });
}

function VideoChatSources({ citations, messageKey, currentContentId, onSeekToSeconds }) {
  const model = useMemo(() => buildSourcesModel(citations), [citations]);
  const hasAny = (model.video?.length || 0) + (model.nonVideo?.length || 0) > 0;
  if (!hasAny) return null;

  const isRedundantChapterTitle = (title) => {
    const normalized = String(title || "").trim();
    if (!normalized) return true;
    return normalized.toLowerCase() === "full lecture";
  };

  return (
    <div className="mt-4 border-t border-white/10 pt-4 text-xs text-gray-200/90">
      <div className="mb-2 font-semibold">Sources</div>

      {!!model.video?.length && (
        <div className="space-y-3">
          {model.video.map((group) => {
            const isCurrentVideo = String(group.contentId) === String(currentContentId || "");
            return (
              <div key={group.contentId}>
                <div className="font-semibold">
                  {group.leaderIndex}. {group.title} <span className="text-gray-300">Video</span>
                </div>
                {(() => {
                  const meaningfulChapters = Array.from(
                    new Set(
                      (group.ranges || [])
                        .map((range) => String(range?.chapterTitle || "").trim())
                        .filter((title) => title && !isRedundantChapterTitle(title))
                    )
                  );
                  const shouldGroupByChapter = meaningfulChapters.length > 1;

                  const renderRangeItem = (range) => {
                    const timeLabel =
                      range.e !== range.s ? `${fmtTimestamp(range.s)}-${fmtTimestamp(range.e)}` : fmtTimestamp(range.s);
                    const showInlineChapter =
                      !shouldGroupByChapter && !isRedundantChapterTitle(range.chapterTitle);
                    const content = isCurrentVideo ? (
                      <button
                        type="button"
                        className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
                        onClick={() => onSeekToSeconds(range.s)}
                      >
                        {timeLabel}
                      </button>
                    ) : range.url ? (
                      <a
                        href={range.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
                      >
                        {timeLabel}
                      </a>
                    ) : (
                      <span>{timeLabel}</span>
                    );

                    return (
                      <li key={`${group.contentId}:${range.s}-${range.e}:${String(range.chapterId || "")}`}>
                        {Array.isArray(range.citationIndices) &&
                          range.citationIndices.map((citationIndex) => (
                            <span
                              key={citationIndex}
                              id={getSourceAnchorId(messageKey, citationIndex)}
                              className="relative -top-20"
                            />
                          ))}
                        <span className="font-semibold">{range.letter}.</span> {content}
                        {showInlineChapter ? (
                          <span className="text-gray-300"> {" - "} {String(range.chapterTitle).trim()}</span>
                        ) : null}
                      </li>
                    );
                  };

                  if (!shouldGroupByChapter) {
                    return <ul className="mt-1 ml-5 list-disc space-y-1">{group.ranges.map(renderRangeItem)}</ul>;
                  }

                  const groupedRanges = new Map();
                  for (const range of group.ranges || []) {
                    const title = String(range?.chapterTitle || "").trim();
                    const key = title && !isRedundantChapterTitle(title) ? title : "Other";
                    if (!groupedRanges.has(key)) groupedRanges.set(key, []);
                    groupedRanges.get(key).push(range);
                  }

                  return (
                    <div className="mt-2 space-y-3">
                      {Array.from(groupedRanges.entries()).map(([chapterTitle, ranges]) => (
                        <div key={chapterTitle}>
                          {chapterTitle !== "Other" ? (
                            <div className="font-medium text-gray-300">{chapterTitle}</div>
                          ) : null}
                          <ul className="mt-1 ml-5 list-disc space-y-1">{ranges.map(renderRangeItem)}</ul>
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
            );
          })}
        </div>
      )}

      {!!model.nonVideo?.length && (
        <div className={`space-y-1 ${model.video?.length ? "mt-4" : ""}`}>
          {model.nonVideo.map(({ i, citation }) => {
            const extra = citation?.extra || {};
            const title = getCitationTitle(citation);
            const pageStart = extra?.pageStart ?? extra?.page_start ?? null;
            const pageEnd = extra?.pageEnd ?? extra?.page_end ?? null;
            const pageLabel =
              pageStart || pageEnd
                ? (() => {
                    const a = Number(pageStart || pageEnd);
                    const b = Number(pageEnd || pageStart);
                    if (Number.isFinite(a) && Number.isFinite(b)) {
                      return a === b ? ` (p.${a})` : ` (p.${a}-${b})`;
                    }
                    return "";
                  })()
                : "";
            const url = typeof citation?.url === "string" ? citation.url : null;

            return (
              <div key={i} className="leading-relaxed">
                <span id={getSourceAnchorId(messageKey, i)} className="relative -top-20" />
                <span className="font-semibold">{i}.</span> {title}
                {pageLabel}
                {url ? (
                  <>
                    {" "}
                    -{" "}
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-purple-300 underline underline-offset-4 hover:text-purple-200"
                    >
                      Open source
                    </a>
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
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
  const [isTyping, setIsTyping] = useState(false);
  const [messages, setMessages] = useState([]);
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
    refetchInterval: (data) => {
      const status = String(data?.status || activeVideoAsset?.status || "").toLowerCase();
      const hasSummary = !!String(data?.aiSummary || "").trim();
      const processing = ["uploaded", "processing", "extracting_audio", "transcribing"].includes(status);
      return !hasSummary && processing ? 2000 : false;
    },
  });

  // Reset per-video transient UI state when navigating between videos.
  useEffect(() => {
    setMessage("");
    setIsTyping(false);
    setMessages([]);
    setIsSummaryExpanded(false);
  }, [courseId, contentId]);

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
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res?.text || "",
          citations: Array.isArray(res?.citations) ? res.citations : [],
        },
      ]);
    } catch (e) {
      const msg = e?.data?.detail || e?.message || "Failed to send message. Please try again.";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Sorry, I encountered an error. ${msg}`, citations: [] },
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
  }, [messages.length, isTyping]);

  const renderChatPanel = ({ scrollHeightClass, variant }) => {
    const isPopout = variant === "popout";
    return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      className="glass-card rounded-2xl overflow-hidden"
    >
      <div className={`flex flex-col ${isPopout ? "" : CARD_HEIGHT_CLASS}`}>
      <div className="p-4 border-b border-white/5 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-purple-400" />
          <h2 className="text-lg font-semibold">Chat</h2>
        </div>
        <div className="flex items-center gap-1">
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

      <div ref={chatScrollRef} className={`${scrollHeightClass} overflow-y-auto px-4 py-4`}>
        <div className="space-y-4">
          {messages.length === 0 && !isTyping && (
            <div className="text-center py-8">
              <Sparkles className="w-10 h-10 text-purple-400 mx-auto mb-2" />
              <p className="text-gray-400 text-sm">Ask questions about the lecture</p>
            </div>
          )}
          {messages.map((msg, index) => {
            const messageKey = String(index);
            const assistantMarkdown = rewriteCitationAnchors(
              String(linkifySummaryTimestamps(msg.content || "") || ""),
              messageKey
            );

            return (
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
                    {msg.role === "assistant" ? (
                      <div className="text-sm leading-relaxed">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                          {assistantMarkdown}
                        </ReactMarkdown>
                        <VideoChatSources
                          citations={msg.citations}
                          messageKey={messageKey}
                          currentContentId={contentId}
                          onSeekToSeconds={seekAndScrollToSeconds}
                        />
                      </div>
                    ) : (
                      <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                    )}
                  </div>
                </div>
              </motion.div>
            );
          })}
          {isTyping && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex justify-start">
              <div className="glass-card rounded-2xl px-4 py-2">
                <Loader2 className="w-4 h-4 animate-spin text-purple-400" />
              </div>
            </motion.div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="p-4 border-t border-white/5 shrink-0">
        <div className="glass-card rounded-xl p-2 flex items-end gap-2">
          <textarea
            ref={textareaRef}
            placeholder="Ask a question..."
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!courseId || isTyping}
            className="flex-1 bg-transparent border-0 text-white placeholder:text-gray-500 resize-none min-h-[44px] max-h-[120px] outline-none ring-0 focus:outline-none focus:ring-0 focus:bg-transparent focus-visible:outline-none focus-visible:ring-0 focus-visible:bg-transparent"
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

