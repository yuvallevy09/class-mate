import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getCourse } from "@/api/courses";
import { listCourseContents, createCourseContent, deleteCourseContent, getDownloadUrl } from "@/api/courseContents";
import { presignUpload, putFileWithProgress } from "@/api/uploads";
import { finalizeVideoUpload, listVideoAssets, transcribeVideoAsset } from "@/api/videoAssets";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { 
  Plus, FileText, Image, Video, File,
  Trash2, Upload, X, BookOpen, Loader2, Search, Grid3x3, List
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { toast } from "@/components/ui/use-toast";
import { createPageUrl } from "@/utils";
import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";

const CATEGORY_LABELS = {
  overview: "Overview",
  media: "Lecture Videos",
};

const FILE_ICONS = {
  pdf: FileText,
  image: Image,
  video: Video,
  default: File
};

function VideoThumbnail({ src, alt, className, fallback }) {
  const [failed, setFailed] = useState(false);
  React.useEffect(() => setFailed(false), [src]);
  if (!src || failed) return fallback;
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={className}
      onError={() => setFailed(true)}
    />
  );
}

// Each pipeline phase → a headline + a context sub-line (shown under the headline).
const STAGE_INFO = {
  uploading: { headline: "Uploading video", sub: "Securely sending your file to ClassMate" },
  finalizing: { headline: "Saving to your library", sub: "Registering the video on the server" },
  uploaded: { headline: "Queued for processing", sub: "Waiting for an available processing worker" },
  processing: { headline: "Preparing video", sub: "Downloading and reading your video" },
  extracting_audio: { headline: "Extracting audio", sub: "Pulling the audio track out of the video" },
  transcribing: {
    headline: "Transcribing with AI",
    sub: "Converting speech into searchable text you can chat with",
  },
};

// The server-side phases that mean "still working" (no playable result yet).
const WORKING_STATES = ["uploaded", "processing", "extracting_audio", "transcribing"];

// A single, monotonic 0–100% across the WHOLE pipeline — it never restarts per stage.
// Each phase owns a [floor, ceil] slice, and the ceil of one phase is the floor of the
// next, so when the stage advances the bar keeps gliding from exactly where it was.
// Upload reports real sub-progress (0–100 from the browser); the server stages can't, so
// we ease asymptotically toward the stage ceiling — always moving, never jumping.
// Extracting audio and transcribing are the long stages, so they get the widest ranges;
// the quick pre-transcribe stages are compressed into the first 40%.
const PROGRESS_RANGES = {
  uploading: [0, 27],
  finalizing: [27, 30],
  uploaded: [30, 35],
  processing: [35, 40],
  extracting_audio: [40, 65],
  transcribing: [65, 100],
};

// Per-phase ease rate for the server creep (fraction of the remaining gap per second).
// Lower = slower climb. Extracting audio and transcribing are the long stages, so they
// creep slowly and their wide ranges fill gradually.
const PROGRESS_SPEED = {
  extracting_audio: 0.12,
  transcribing: 0.05,
};

const progressStorageKey = (key) => (key ? `vprog:${key}` : null);

function readStoredProgress(key) {
  const sk = progressStorageKey(key);
  if (!sk) return 0;
  try {
    return Number(window.localStorage.getItem(sk)) || 0;
  } catch {
    return 0;
  }
}

// Forget a persisted progress value (call when the item is done or deleted).
function clearStoredProgress(key) {
  const sk = progressStorageKey(key);
  if (!sk) return;
  try {
    window.localStorage.removeItem(sk);
  } catch {
    /* ignore */
  }
}

// Drives a continuously-animating progress value with requestAnimationFrame so the bar
// never sits still mid-stage and never snaps between stages. Reads the latest phase /
// upload % from a ref so the rAF loop runs once for the card's lifetime.
//
// `persistKey` (the content/asset id) is used to remember the value in localStorage so a
// page refresh RESUMES from where the bar actually was, instead of resetting to the start
// of the current stage — which felt unnatural for the long transcribing stage.
function useSmoothProgress(phase, uploadPct, persistKey) {
  const [display, setDisplay] = useState(() => readStoredProgress(persistKey));
  const stateRef = React.useRef({ value: display, phase, uploadPct, persistKey });
  stateRef.current.phase = phase;
  stateRef.current.uploadPct = uploadPct;
  stateRef.current.persistKey = persistKey;

  React.useEffect(() => {
    let raf;
    let last = performance.now();
    let lastWrite = last;
    const tick = (now) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const { value: cur, phase: ph, uploadPct: up, persistKey: pk } = stateRef.current;
      const [floor, ceil] = PROGRESS_RANGES[ph] || [50, 60];

      let target;
      let speed;
      if (ph === "uploading") {
        // Track the real browser upload number closely.
        target = floor + (ceil - floor) * Math.min(1, Math.max(0, (up || 0) / 100));
        speed = 8;
      } else {
        // No real signal — creep toward the stage ceiling.
        target = ceil;
        speed = PROGRESS_SPEED[ph] ?? 0.7;
      }

      let next = cur + (target - cur) * Math.min(1, speed * dt);
      if (next < floor) next = floor;              // a later stage bumps the floor up
      if (next < cur) next = cur;                  // strictly monotonic
      if (ph !== "uploading") next = Math.min(next, ceil - 0.4); // leave the final step to the next stage
      next = Math.min(next, 99.5);

      stateRef.current.value = next;
      setDisplay(next);

      // Persist at ~2Hz so a refresh resumes from roughly the same number.
      const sk = progressStorageKey(pk);
      if (sk && now - lastWrite > 500) {
        lastWrite = now;
        try {
          window.localStorage.setItem(sk, String(Math.round(next * 10) / 10));
        } catch {
          /* ignore */
        }
      }

      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return display;
}

// Big gradient "hero" tile with a centered spinner. Shown for the whole load process —
// uploading, finalizing, then server-side processing — until a real thumbnail exists.
function ProcessingMedia({ viewMode }) {
  return (
    <div
      className={`${
        viewMode === "grid" ? "w-full aspect-video mb-5" : "w-20 h-12 shrink-0"
      } rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center`}
    >
      <Loader2
        className={`${viewMode === "grid" ? "w-7 h-7" : "w-5 h-5"} text-purple-400 animate-spin`}
      />
    </div>
  );
}

// Stage headline + context sub-line + the shared monotonic progress bar.
// The bar is driven by rAF (no CSS width transition) so it glides continuously; the
// sub-line crossfades whenever the stage (headline) changes.
function SmoothProgressRow({ phase, uploadPct = 0, persistKey }) {
  const value = useSmoothProgress(phase, uploadPct, persistKey);
  const info = STAGE_INFO[phase] || STAGE_INFO.processing;
  return (
    <div className="mt-4">
      <div className="flex items-center justify-between text-xs text-gray-300 mb-1">
        <span>{info.headline}</span>
        <span>{Math.round(value)}%</span>
      </div>
      <div className="min-h-[16px] mb-2">
        <AnimatePresence mode="wait">
          <motion.div
            key={info.headline}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.35 }}
            className="text-[11px] leading-snug text-gray-400"
          >
            {info.sub}
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="h-2 w-full rounded-full bg-white/10 overflow-hidden">
        <div className="h-full bg-purple-500/70" style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

// Body of the placeholder card shown while an upload (and the server processing that
// follows it) runs in the background. The single card stays mounted for the whole
// lifecycle — upload → finalize → processing → transcribing — so nothing flashes between
// stages. `phase` is the resolved pipeline phase (local while uploading, then the live
// video-asset status). The outer card wrapper is provided by the caller.
function UploadCardBody({ upload, phase, viewMode, onRetry, onDismiss }) {
  const isError = phase === "error";
  const Icon = upload.isVideo ? Video : File;
  return (
    <div className={viewMode === "grid" ? "" : "flex items-start gap-4"}>
      {isError ? (
        <div
          className={`${
            viewMode === "grid" ? "w-full aspect-video mb-5" : "w-20 h-12 shrink-0"
          } rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center`}
        >
          <Icon className="w-6 h-6 text-purple-400" />
        </div>
      ) : (
        <ProcessingMedia viewMode={viewMode} />
      )}
      <div className="flex-1 min-w-0">
        <h3 className="font-semibold mb-2 pr-8 truncate">{upload.title}</h3>
        {isError ? (
          <>
            <p className="text-xs text-red-200/80 line-clamp-2 mb-3" title={upload.error}>
              {upload.error}
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                onClick={() => onRetry(upload)}
                className="h-8 px-3 rounded-full bg-white/5 hover:bg-white/10 text-gray-200"
              >
                Retry
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => onDismiss(upload.id)}
                className="h-8 px-3 rounded-full text-gray-400 hover:text-white"
              >
                Dismiss
              </Button>
            </div>
          </>
        ) : (
          <SmoothProgressRow
            phase={phase}
            uploadPct={upload.progress}
            persistKey={upload.contentId || upload.id}
          />
        )}
      </div>
    </div>
  );
}

export default function CourseContent() {
  const navigate = useNavigate();
  const urlParams = new URLSearchParams(window.location.search);
  const courseId = urlParams.get("courseId");
  const category = urlParams.get("category");
  const isVideosPage = category === "media";
  const videoUploadMaxSizeMb = Number(import.meta.env.VITE_UPLOAD_MAX_SIZE_MB) || 100;
  // Human-friendly label: show GB once we're at/above 1024MB, otherwise MB.
  const videoUploadMaxSizeLabel =
    videoUploadMaxSizeMb >= 1024
      ? `${(videoUploadMaxSizeMb / 1024).toFixed(videoUploadMaxSizeMb % 1024 === 0 ? 0 : 1)}GB`
      : `${videoUploadMaxSizeMb}MB`;
  
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  // In-flight uploads shown as placeholder cards after the dialog closes.
  // Each: { id, title, description, file, isVideo, progress, phase, error }
  const [uploads, setUploads] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState("grid");
  const [newContent, setNewContent] = useState({ title: "", description: "", file: null });
  const [kickoffNotice, setKickoffNotice] = useState(null);
  const uploadSeq = React.useRef(0);

  const queryClient = useQueryClient();

  const { data: course } = useQuery({
    queryKey: ['course', courseId],
    queryFn: async () => getCourse(courseId),
    enabled: !!courseId
  });

  const { data: content = [], isLoading } = useQuery({
    queryKey: ['content', courseId, category],
    queryFn: () => listCourseContents(courseId, { category }),
    enabled: !!courseId && !!category
  });

  // Stage-based processing UI for videos: poll while any are still processing.
  const { data: videoAssets = [] } = useQuery({
    queryKey: ['videoAssets', courseId],
    queryFn: () => listVideoAssets(courseId),
    enabled: !!courseId && category === "media",
    // React Query v5: the callback receives the Query object, so read query.state.data.
    refetchInterval: (query) => {
      const items = query?.state?.data || [];
      const anyProcessing = Array.isArray(items) && items.some(a => ["uploaded", "processing", "extracting_audio", "transcribing"].includes(a.status));
      return anyProcessing ? 2000 : false;
    },
  });

  const videoAssetByContentId = useMemo(() => {
    const m = new Map();
    for (const a of (Array.isArray(videoAssets) ? videoAssets : [])) {
      if (a?.content_id) m.set(a.content_id, a);
    }
    return m;
  }, [videoAssets]);

  const retryTranscriptionMutation = useMutation({
    mutationFn: async (videoAssetId) => {
      if (!videoAssetId) throw new Error("videoAssetId is required");
      return transcribeVideoAsset(videoAssetId, { force: true });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['videoAssets', courseId] });
    },
  });

  const updateUpload = (id, patch) =>
    setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...patch } : u)));

  const dismissUpload = (id) =>
    setUploads((prev) => prev.filter((u) => u.id !== id));

  // Maps a finished content id → the placeholder card's id. Reusing that key for the real
  // content card keeps the SAME React element across the swap, so the card never unmounts/
  // remounts — no flash between the processing placeholder and the finished card.
  const handoffKeys = React.useRef(new Map());
  const finishUpload = (id, contentId) => {
    if (contentId) {
      handoffKeys.current.set(contentId, id);
      clearStoredProgress(contentId);
    }
    setUploads((prev) => prev.filter((u) => u.id !== id));
  };

  // Once a tracked video asset leaves the working states (done or errored), retire its
  // placeholder card — recording the handoff first so the real content card adopts the
  // same key and the transition is seamless.
  React.useEffect(() => {
    setUploads((prev) => {
      const finished = prev.filter((u) => {
        if (!u.contentId) return false;
        const a = videoAssetByContentId.get(u.contentId);
        return a && !WORKING_STATES.includes(a.status);
      });
      if (!finished.length) return prev;
      finished.forEach((u) => {
        handoffKeys.current.set(u.contentId, u.id);
        clearStoredProgress(u.contentId);
      });
      const drop = new Set(finished.map((u) => u.id));
      return prev.filter((u) => !drop.has(u.id));
    });
  }, [videoAssetByContentId]);

  // Live display state for a content item (video processing stage, thumbnail, etc.).
  const deriveContentState = (item) => {
    const mt = (item.mime_type || "").toLowerCase();
    const isVideo = mt.startsWith("video/");
    const asset = isVideo ? (videoAssetByContentId.get(item.id) || null) : null;
    const stage = asset?.status || null;
    const isError = stage === "error";
    const isWorking = WORKING_STATES.includes(stage);
    const hasThumb = isVideo && !!asset?.thumbnail_url;
    // Keep the big spinner hero until we actually have a thumbnail to show.
    const showProcessingHero = isVideo && isWorking && !hasThumb;
    const doneLabel =
      stage === "done_no_embeddings" ? "Indexed (lexical only)" :
      stage === "done_no_index" ? "Transcribed (not indexed)" :
      null;
    return { isVideo, asset, stage, isError, isWorking, hasThumb, showProcessingHero, doneLabel };
  };

  // Drives a single upload in the background after the dialog has already closed.
  // Progress + errors are surfaced on a placeholder card, not in the dialog.
  const runUpload = async (upload) => {
    const { id, title, description, file, isVideo } = upload;
    try {
      updateUpload(id, { phase: "uploading", progress: 0, error: null });

      if (file) {
        // Client-side max size guard (matches the UI hint).
        if (isVideo) {
          const maxBytes = Math.max(0, videoUploadMaxSizeMb) * 1024 * 1024;
          const sizeBytes = Number(file.size || 0);
          if (maxBytes > 0 && sizeBytes > maxBytes) {
            throw new Error(
              `Video is too large (${Math.ceil(sizeBytes / (1024 * 1024))}MB). Max allowed is ${videoUploadMaxSizeLabel}.`
            );
          }
        }

        const presign = await presignUpload({ courseId, file });
        await putFileWithProgress({
          url: presign.uploadUrl,
          method: presign.method,
          file,
          onProgress: (pct) => updateUpload(id, { progress: pct }),
        });

        updateUpload(id, { phase: "finalizing", progress: 100 });

        if (isVideo) {
          // This step is REQUIRED. If it fails, we should not pretend the upload "worked"
          // since the app won't be able to list/play the new video.
          const res = await finalizeVideoUpload(courseId, {
            title,
            description,
            source_file_key: presign.key,
            original_filename: file.name,
            mime_type: file.type || "application/octet-stream",
            size_bytes: file.size ?? null,
            kickoffTranscription: false,
          });

          const videoAsset = res?.videoAsset;
          if (!videoAsset?.id) {
            throw new Error("Upload succeeded, but the server did not return a video asset id.");
          }

          // Hand the SAME placeholder card over to live server-status tracking. We record
          // the content id so the real (now-existing) content card is hidden as a duplicate
          // while this card keeps showing one continuous progress bar through processing and
          // transcribing. The cleanup effect dismisses it — with a seamless, same-key
          // handoff to the real card — only once the asset finishes.
          updateUpload(id, {
            phase: "uploaded",
            progress: 100,
            contentId: videoAsset.content_id || res?.content?.id || null,
            videoAssetId: videoAsset.id,
          });

          // Fire-and-forget: if transcription fails, the asset is marked "error" and can be retried on its card.
          transcribeVideoAsset(videoAsset.id, { force: false }).catch((e) => {
            console.error("Video transcription kickoff failed:", e);
            setKickoffNotice({
              type: "error",
              message:
                "Upload succeeded, but transcription didn’t start. Please check your config, or click Retry on the video card.",
            });
            window.setTimeout(() => setKickoffNotice(null), 8000);
          });

          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['content', courseId, category] }),
            queryClient.invalidateQueries({ queryKey: ['videoAssets', courseId] }),
          ]);
          // Keep the card mounted; live asset status drives it from here.
          return;
        }

        const created = await createCourseContent(courseId, {
          category,
          title,
          description,
          file_key: presign.key,
          original_filename: file.name,
          mime_type: file.type || "application/octet-stream",
          size_bytes: file.size ?? null,
        });
        await queryClient.invalidateQueries({ queryKey: ['content', courseId, category] });
        finishUpload(id, created?.id || null);
        return;
      }

      // No file — a plain content entry (no upload/processing stages).
      const created = await createCourseContent(courseId, { category, title, description });
      await queryClient.invalidateQueries({ queryKey: ['content', courseId, category] });
      finishUpload(id, created?.id || null);
    } catch (err) {
      const detail =
        (typeof err?.data?.detail === "string" && err.data.detail) ||
        (typeof err?.message === "string" && err.message) ||
        "Something went wrong. Please try again.";
      updateUpload(id, { phase: "error", error: detail });
      toast({
        title: isVideosPage ? "Couldn’t add video" : "Couldn’t add content",
        description: detail,
      });
    }
  };

  const deleteContentMutation = useMutation({
    mutationFn: (contentId) => {
      clearStoredProgress(contentId);
      return deleteCourseContent(contentId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['content', courseId, category] });
    }
  });

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setNewContent({ ...newContent, file: e.target.files[0] });
    }
  };

  const handleCreate = () => {
    const title = newContent.title.trim();
    if (!title) return;

    const file = newContent.file || null;
    const upload = {
      id: `upload-${uploadSeq.current++}`,
      title,
      description: newContent.description,
      file,
      isVideo: (file?.type || "").toLowerCase().startsWith("video/"),
      progress: 0,
      phase: "uploading",
      error: null,
    };

    // Close the dialog right away and let the upload run in the background.
    setIsAddDialogOpen(false);
    setNewContent({ title: "", description: "", file: null });
    setUploads((prev) => [...prev, upload]);
    runUpload(upload);
  };

  const getFileIcon = (mimeType) => {
    const mt = (mimeType || "").toLowerCase();
    if (!mt) return FILE_ICONS.default;
    if (mt === "application/pdf" || mt.endsWith("/pdf")) return FILE_ICONS.pdf;
    if (mt.startsWith("image/")) return FILE_ICONS.image;
    if (mt.startsWith("video/")) return FILE_ICONS.video;
    return FILE_ICONS.default;
  };

  const handleViewFile = async (item) => {
    try {
      const mt = (item?.mime_type || "").toLowerCase();
      if (mt.startsWith("video/")) {
        navigate(createPageUrl(`VideoPlayer?courseId=${courseId}&contentId=${item.id}`));
        return;
      }
      const res = await getDownloadUrl(item.id);
      if (!res?.url) return;
      window.open(res.url, "_blank", "noopener,noreferrer");
    } catch (e) {
      console.error(e);
    }
  };

  const filteredContent = content.filter(item =>
    item.title?.toLowerCase().includes(searchQuery.toLowerCase()) ||
    item.description?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // One unified, de-duplicated list so each item is a SINGLE card for its whole life.
  // Active uploads render as placeholders; their content ids are hidden from the content
  // list (no duplicate). When an upload finishes it's removed and the real content card
  // adopts the same key via handoffKeys — the wrapper element persists, so the swap from
  // "processing" to "done" happens in place with nothing disappearing or re-entering.
  const trackedContentIds = new Set(uploads.map((u) => u.contentId).filter(Boolean));
  const cards = [
    ...uploads.map((u) => {
      // Before finalize, the local phase drives it; after, the live asset status does.
      const liveAsset = u.contentId ? videoAssetByContentId.get(u.contentId) : null;
      const phase =
        u.phase === "error"
          ? "error"
          : liveAsset && WORKING_STATES.includes(liveAsset.status)
          ? liveAsset.status
          : u.phase;
      return { type: "upload", key: u.id, upload: u, phase };
    }),
    ...filteredContent
      .filter((item) => !trackedContentIds.has(item.id))
      .map((item) => ({
        type: "content",
        key: handoffKeys.current.get(item.id) || item.id,
        item,
        state: deriveContentState(item),
      })),
  ];

  return (
    <div className="min-h-screen relative flex flex-col">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-0 right-1/4 w-[500px] h-[500px] bg-purple-500/5 rounded-full blur-[150px]" />
        <div className="absolute bottom-0 left-1/4 w-[500px] h-[500px] bg-pink-500/5 rounded-full blur-[150px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen(!isSidebarOpen)} showMenu={true} />

      {/* Main Content */}
      <main className="relative z-10 px-6 lg:px-16 py-8">
        <div className="max-w-7xl mx-auto">
          {category === "media" && kickoffNotice?.message && (
            <div
              className={`mb-6 rounded-2xl border px-4 py-3 flex items-start justify-between gap-4 ${
                kickoffNotice.type === "error"
                  ? "border-red-500/20 bg-red-500/10 text-red-100"
                  : "border-white/10 bg-white/5 text-gray-200"
              }`}
            >
              <div className="text-sm leading-relaxed">{kickoffNotice.message}</div>
              <button
                type="button"
                onClick={() => setKickoffNotice(null)}
                className="shrink-0 p-1 rounded-lg hover:bg-white/10"
                aria-label="Dismiss"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Page Header */}
          <div className="flex items-start justify-between mb-8">
            <div>
              <div className="flex items-center gap-2 text-sm text-gray-400 mb-1">
                <BookOpen className="w-4 h-4" />
                <span>{course?.name}</span>
              </div>
              <h1 className="text-3xl font-bold mb-2">{CATEGORY_LABELS[category] || category}</h1>
              <p className="text-gray-400">View and manage your course materials.</p>
            </div>
            <Button
              onClick={() => setIsAddDialogOpen(true)}
              className="btn-gradient rounded-full px-5 py-3 h-auto font-semibold whitespace-nowrap"
            >
              <Plus className="w-5 h-5 mr-2" />
              {isVideosPage ? "Add Video" : "Add Content"}
            </Button>
          </div>

          {/* Search Bar */}
          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between mb-8">
            <div className="relative flex-1 max-w-md w-full">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
              <Input
                placeholder={isVideosPage ? "Search videos..." : "Search content..."}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-12 h-12 bg-white/5 border-white/10 rounded-xl text-white placeholder:text-gray-500 focus:border-purple-500/50 focus:ring-purple-500/20 w-full"
              />
            </div>
            <ToggleGroup type="single" value={viewMode} onValueChange={(value) => value && setViewMode(value)}>
              <ToggleGroupItem 
                value="grid" 
                className="data-[state=on]:bg-purple-500/20 data-[state=on]:text-white border border-white/10 hover:bg-white/5"
              >
                <Grid3x3 className="w-4 h-4 mr-2" />
                Grid
              </ToggleGroupItem>
              <ToggleGroupItem 
                value="list"
                className="data-[state=on]:bg-purple-500/20 data-[state=on]:text-white border border-white/10 hover:bg-white/5"
              >
                <List className="w-4 h-4 mr-2" />
                List
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

          {isLoading ? (
            <div className={viewMode === "grid" ? "grid md:grid-cols-2 lg:grid-cols-3 gap-6" : "space-y-4"}>
              {[1, 2, 3].map(i => (
                <div key={i} className="glass-card rounded-2xl p-6 animate-pulse">
                  <div className={viewMode === "grid" ? "" : "flex items-start gap-4"}>
                    <div className={`${viewMode === "grid" ? "w-12 h-12 mb-5" : "w-12 h-12"} rounded-xl bg-white/10 shrink-0`} />
                    <div className="flex-1">
                      <div className="h-5 bg-white/10 rounded mb-3 w-3/4" />
                      <div className="h-4 bg-white/5 rounded w-full" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : content.length === 0 && uploads.length === 0 ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-center py-20"
            >
              <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center mx-auto mb-6">
                {isVideosPage ? (
                  <Video className="w-10 h-10 text-purple-400" />
                ) : (
                  <FileText className="w-10 h-10 text-purple-400" />
                )}
              </div>
              <h3 className="text-xl font-semibold mb-2">{isVideosPage ? "No videos yet" : "No content yet"}</h3>
              <p className="text-gray-400 mb-6">
                {isVideosPage
                  ? "Add your first video to start building course context"
                  : `Add your first ${CATEGORY_LABELS[category]?.toLowerCase()} item`}
              </p>
              <Button
                onClick={() => setIsAddDialogOpen(true)}
                className="btn-gradient rounded-full px-6 py-3 h-auto font-semibold"
              >
                <Plus className="w-4 h-4 mr-2" />
                {isVideosPage ? "Add Video" : "Add Content"}
              </Button>
            </motion.div>
          ) : (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className={viewMode === "grid" ? "grid md:grid-cols-2 lg:grid-cols-3 gap-6" : "space-y-4"}
            >
              <AnimatePresence>
                {cards.map((card) => {
                  // Same wrapper element type + stable key across the upload→content swap,
                  // so React reconciles in place instead of unmounting/remounting (no flash).
                  if (card.type === "upload") {
                    return (
                      <motion.div
                        key={card.key}
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.9 }}
                        transition={{ duration: 0.25 }}
                        className="glass-card rounded-2xl p-6 relative group"
                      >
                        <UploadCardBody
                          upload={card.upload}
                          phase={card.phase}
                          viewMode={viewMode}
                          onRetry={runUpload}
                          onDismiss={dismissUpload}
                        />
                      </motion.div>
                    );
                  }

                  const { item } = card;
                  const { isVideo, asset, isError, isWorking, showProcessingHero, doneLabel } = card.state;
                  const IconComponent = getFileIcon(item.mime_type);
                  return (
                    <motion.div
                      key={card.key}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.9 }}
                      transition={{ duration: 0.25 }}
                      whileHover={{ y: -3 }}
                      className={`glass-card rounded-2xl p-6 group relative ${isError ? "opacity-75" : ""}`}
                    >
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => deleteContentMutation.mutate(item.id)}
                        className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-400 hover:bg-red-500/10"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>

                      <div className={viewMode === "grid" ? "" : "flex items-start gap-4"}>
                        {showProcessingHero ? (
                          <ProcessingMedia viewMode={viewMode} />
                        ) : (
                          <VideoThumbnail
                            src={isVideo ? asset?.thumbnail_url || null : null}
                            alt={item.title}
                            className={
                              viewMode === "grid"
                                ? "w-full aspect-video object-cover rounded-xl mb-5 bg-black/40"
                                : "w-20 h-12 object-cover rounded-lg shrink-0 bg-black/40"
                            }
                            fallback={
                              <div className={`${viewMode === "grid" ? "w-12 h-12 mb-5" : "w-12 h-12"} rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center shrink-0`}>
                                <IconComponent className="w-6 h-6 text-purple-400" />
                              </div>
                            }
                          />
                        )}

                        <div className="flex-1">
                          <h3 className="font-semibold mb-2 pr-8">{item.title}</h3>

                          {item.description && (
                            <p className="text-sm text-gray-400 line-clamp-2 mb-4">
                              {item.description}
                            </p>
                          )}

                          {item.file_key && !showProcessingHero && (
                            <button
                              type="button"
                              onClick={() => handleViewFile(item)}
                              className="inline-flex items-center gap-2 text-sm text-purple-400 hover:text-purple-300 transition-colors"
                            >
                              {isVideo ? <Video className="w-4 h-4" /> : <File className="w-4 h-4" />}
                              {isVideo ? "View Video" : "View File"}
                            </button>
                          )}

                          {isVideo && isWorking && (
                            <SmoothProgressRow phase={card.state.stage} persistKey={item.id} />
                          )}

                          {isVideo && !isWorking && !isError && doneLabel && (
                            <div className="mt-4">
                              <div className="inline-flex items-center gap-2 text-xs font-semibold text-gray-300 bg-white/5 border border-white/10 rounded-full px-3 py-1">
                                {doneLabel}
                              </div>
                            </div>
                          )}

                          {isVideo && isError && (
                            <div className="mt-4">
                              <div className="flex items-center justify-between gap-3">
                                <div className="inline-flex items-center gap-2 text-xs font-semibold text-red-300 bg-red-500/10 border border-red-500/20 rounded-full px-3 py-1">
                                  Transcription failed
                                </div>
                                <Button
                                  type="button"
                                  size="sm"
                                  onClick={() => retryTranscriptionMutation.mutate(asset.id)}
                                  disabled={retryTranscriptionMutation.isPending}
                                  className="h-8 px-3 rounded-full bg-white/5 hover:bg-white/10 text-gray-200"
                                >
                                  {retryTranscriptionMutation.isPending ? "Retrying…" : "Retry"}
                                </Button>
                              </div>
                              {asset?.transcription_error && (
                                <p
                                  className="mt-2 text-xs text-red-200/80 line-clamp-2"
                                  title={asset.transcription_error}
                                >
                                  {asset.transcription_error}
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </motion.div>
          )}
        </div>
      </main>

      {/* Add Content Dialog */}
      <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
        <DialogContent className="bg-[#131313] border-white/10 text-white max-w-md">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">
              {isVideosPage ? "Add Video" : `Add ${CATEGORY_LABELS[category]}`}
            </DialogTitle>
            <DialogDescription className="text-gray-400">
              {isVideosPage
                ? "Upload a new video so ClassMate can follow along with your course."
                : "Add a new item to this course category. Attach a PDF to enable retrieval-augmented answers in chat."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5 pt-4">
            <div>
              <Label className="text-gray-300 mb-2 block">Title *</Label>
              <Input
                placeholder="Content title"
                value={newContent.title}
                onChange={(e) => setNewContent({ ...newContent, title: e.target.value })}
                className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50"
              />
            </div>
            <div>
              <Label className="text-gray-300 mb-2 block">Description</Label>
              <Textarea
                placeholder="Brief description..."
                value={newContent.description}
                onChange={(e) => setNewContent({ ...newContent, description: e.target.value })}
                className="bg-white/5 border-white/10 text-white placeholder:text-gray-500 focus:border-purple-500/50 min-h-[80px]"
              />
            </div>
            <div>
              <Label className="text-gray-300 mb-2 block">{isVideosPage ? "Upload Video" : "Upload File"}</Label>
              <div className="relative">
                <input
                  type="file"
                  onChange={handleFileChange}
                  accept={isVideosPage ? "video/*" : undefined}
                  className="hidden"
                  id="file-upload"
                />
                <label
                  htmlFor="file-upload"
                  className="flex items-center justify-center gap-3 w-full h-24 border-2 border-dashed border-white/10 rounded-xl cursor-pointer hover:border-purple-500/50 transition-colors"
                >
                  {newContent.file ? (
                    <div className="flex items-center gap-2 text-purple-400">
                      <File className="w-5 h-5" />
                      <span className="text-sm">{newContent.file.name}</span>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.preventDefault();
                          setNewContent({ ...newContent, file: null });
                        }}
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ) : (
                    <div className="text-center">
                      <Upload className="w-6 h-6 text-gray-500 mx-auto mb-2" />
                      <span className="text-sm text-gray-400">
                        {isVideosPage ? "Click to upload a video" : "Click to upload"}
                      </span>
                    </div>
                  )}
                </label>
              </div>
              {isVideosPage && (
                <div className="mt-2 text-xs text-gray-400 leading-relaxed">
                  <div>
                    Supported formats: <span className="text-gray-200">MP4</span>,{" "}
                    <span className="text-gray-200">MOV</span>,{" "}
                    <span className="text-gray-200">WebM</span>.
                  </div>
                  <div>
                    Max size: <span className="text-gray-200">{videoUploadMaxSizeLabel}</span>.
                  </div>
                </div>
              )}
            </div>
            <Button
              onClick={handleCreate}
              disabled={!newContent.title.trim()}
              className="w-full btn-gradient rounded-xl h-12 font-semibold"
            >
              {isVideosPage ? "Add Video" : "Add Content"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <CourseSidebar
        courseId={courseId}
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        activeCategory={category}
      />
    </div>
  );
}