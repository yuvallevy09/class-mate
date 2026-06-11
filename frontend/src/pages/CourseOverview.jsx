import React, { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  Video,
  ChevronDown,
  ChevronUp,
  Loader2,
  Plus,
} from "lucide-react";

import { createPageUrl, courseGradient } from "@/utils";
import { getCourse } from "@/api/courses";
import { listCourseContents } from "@/api/courseContents";
import { listVideoAssets } from "@/api/videoAssets";
import { Button } from "@/components/ui/button";
import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";

// ---------------------------------------------------------------------------
// Increment 1: stub content only. Real lecture data + summary endpoint get
// wired in later increments (see plan); the layout below is the contract.
// ---------------------------------------------------------------------------

const STUB_SUMMARY_PARAGRAPHS = [
  "This course has built a foundation in linear algebra, starting from the geometry and algebra of vectors and the axioms that define a vector space. You learned to test sets for closure under addition and scaling, and to reason about subspaces, span, and linear independence as the language for describing structure inside a space.",
  "From there, the course moved to matrices as representations of linear maps. You practiced matrix multiplication as composition of maps, studied the column space and null space, and connected solving Ax = b to questions about rank and invertibility. Determinants were introduced both computationally and as signed volume, giving a concrete test for when a matrix is invertible.",
  "Most recently, the lectures turned to eigenvalues and eigenvectors: finding them via the characteristic polynomial, interpreting them as directions a map merely stretches, and using them to understand the long-run behavior of repeated transformations. This sets up diagonalization, which the latest lecture begins to develop.",
];

const PROCESSING_STATUSES = ["processing", "extracting_audio", "transcribing"];

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function SummaryContent({ state, lectureCount }) {
  const [expanded, setExpanded] = useState(false);

  if (state === "empty") {
    return (
      <p className="text-gray-500 leading-relaxed">
        Once you upload your first lecture, ClassMate will write a running
        summary of what the course has covered so far — and keep it up to date
        with every new lecture.
      </p>
    );
  }

  if (state === "generating") {
    return (
      <div>
        <div className="flex items-center gap-2 text-sm text-purple-300 mb-4">
          <Loader2 className="w-4 h-4 animate-spin" />
          Summarizing Lecture {lectureCount}…
        </div>
        <div className="space-y-3 animate-pulse">
          <div className="h-4 bg-white/10 rounded w-full" />
          <div className="h-4 bg-white/10 rounded w-11/12" />
          <div className="h-4 bg-white/5 rounded w-2/3" />
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="relative">
        <div className={expanded ? "" : "max-h-44 overflow-hidden"}>
          {STUB_SUMMARY_PARAGRAPHS.map((p, i) => (
            <p key={i} className="text-gray-300 leading-relaxed mb-4 last:mb-0">
              {p}
            </p>
          ))}
        </div>
        {!expanded && (
          <div className="absolute bottom-0 inset-x-0 h-16 bg-gradient-to-t from-[#0F0F0F] to-transparent pointer-events-none" />
        )}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-purple-400 hover:text-purple-300 transition-colors"
      >
        {expanded ? (
          <>
            Show less <ChevronUp className="w-4 h-4" />
          </>
        ) : (
          <>
            Read more <ChevronDown className="w-4 h-4" />
          </>
        )}
      </button>
    </div>
  );
}

function LectureRow({ lecture, index, courseId }) {
  const isProcessing = lecture.isProcessing;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.1 + index * 0.06 }}
      className="relative flex gap-4 group"
    >
      <div className="relative z-10 w-10 h-10 rounded-full glass-card flex items-center justify-center shrink-0 mt-4 text-sm font-semibold text-purple-300">
        {String(index + 1).padStart(2, "0")}
      </div>

      {isProcessing ? (
        <div className="flex-1 glass-card rounded-2xl p-5 opacity-70">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="font-semibold text-gray-300">{lecture.title}</h3>
            <span className="inline-flex items-center gap-2 text-xs text-purple-300 shrink-0">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Generating description…
            </span>
          </div>
          <div className="space-y-2 animate-pulse">
            <div className="h-3.5 bg-white/10 rounded w-full" />
            <div className="h-3.5 bg-white/5 rounded w-3/4" />
          </div>
        </div>
      ) : (
        <Link
          to={createPageUrl(`VideoPlayer?courseId=${courseId}&contentId=${lecture.id}`)}
          className="flex-1 glass-card rounded-2xl p-5 transition-all duration-300 hover:border-purple-500/30 hover:neon-glow block"
        >
          <div className="flex items-start gap-4">
            <div className="flex-1 min-w-0">
              <h3 className="font-semibold mb-1.5 group-hover:gradient-text transition-all">
                {lecture.title}
              </h3>
              <p className="text-sm text-gray-400 leading-relaxed mb-3">
                {lecture.description || "No description yet."}
              </p>
              <span className="text-xs text-gray-500">{lecture.date}</span>
            </div>
            <div className="hidden sm:flex w-28 h-16 rounded-lg bg-black/40 border border-white/5 items-center justify-center shrink-0 overflow-hidden">
              {lecture.thumbnailUrl ? (
                <img
                  src={lecture.thumbnailUrl}
                  alt={lecture.title}
                  loading="lazy"
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                  }}
                />
              ) : (
                <Video className="w-5 h-5 text-gray-600" />
              )}
            </div>
          </div>
        </Link>
      )}
    </motion.div>
  );
}

function EmptyCourseState({ courseId }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="border-2 border-dashed border-white/10 rounded-2xl py-16 px-8 text-center"
    >
      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-pink-500/10 via-purple-500/10 to-blue-500/10 flex items-center justify-center mx-auto mb-6">
        <Video className="w-8 h-8 text-purple-400" />
      </div>
      <h3 className="text-xl font-semibold mb-2 text-gray-200">
        Your course starts with the first lecture
      </h3>
      <p className="text-gray-500 mb-8 max-w-md mx-auto">
        Upload a lecture video and ClassMate will transcribe it, describe it,
        and start building your course summary — right here.
      </p>
      <Link to={createPageUrl(`CourseContent?courseId=${courseId}&category=media`)}>
        <Button className="btn-gradient rounded-full px-6 py-3 h-auto font-semibold">
          <Plus className="w-4 h-4 mr-2" />
          Upload First Lecture
        </Button>
      </Link>

      {/* Ghost preview of what the page will become */}
      <div className="mt-12 max-w-lg mx-auto space-y-3 opacity-30 pointer-events-none select-none">
        <div className="h-4 bg-white/10 rounded w-1/3 mx-auto" />
        <div className="h-3 bg-white/5 rounded w-full" />
        <div className="h-3 bg-white/5 rounded w-5/6 mx-auto" />
        <div className="h-3 bg-white/5 rounded w-2/3 mx-auto" />
      </div>
    </motion.div>
  );
}

export default function CourseOverview() {
  const urlParams = new URLSearchParams(window.location.search);
  const courseId = urlParams.get("id");

  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  const { data: course } = useQuery({
    queryKey: ["course", courseId],
    queryFn: () => getCourse(courseId),
    enabled: !!courseId,
  });

  const { data: content = [], isLoading } = useQuery({
    queryKey: ["content", courseId, "media"],
    queryFn: () => listCourseContents(courseId, { category: "media" }),
    enabled: !!courseId,
  });

  // Poll while any video is still processing so descriptions appear live.
  const { data: videoAssets = [] } = useQuery({
    queryKey: ["videoAssets", courseId],
    queryFn: () => listVideoAssets(courseId),
    enabled: !!courseId,
    refetchInterval: (data) => {
      const items = data || [];
      const anyProcessing =
        Array.isArray(items) && items.some((a) => PROCESSING_STATUSES.includes(a.status));
      return anyProcessing ? 2000 : false;
    },
  });

  const lectures = useMemo(() => {
    const assetByContentId = new Map();
    for (const a of Array.isArray(videoAssets) ? videoAssets : []) {
      if (a?.content_id) assetByContentId.set(a.content_id, a);
    }
    return [...content]
      .sort((a, b) => String(a?.created_at || "").localeCompare(String(b?.created_at || "")))
      .map((item) => {
        const asset = assetByContentId.get(item.id) || null;
        return {
          id: item.id,
          title: item.title,
          description: asset?.ai_description || item.description || null,
          date: formatDate(item.created_at),
          isProcessing: !!asset && PROCESSING_STATUSES.includes(asset.status),
          thumbnailUrl: asset?.thumbnail_url || null,
        };
      });
  }, [content, videoAssets]);

  const gradient = courseGradient(courseId);
  const isEmpty = !isLoading && lectures.length === 0;
  const lastUpdated = lectures.length ? lectures[lectures.length - 1].date : null;

  return (
    <div className="min-h-screen relative">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className={`absolute -top-24 left-1/3 w-[500px] h-[500px] bg-gradient-to-br ${gradient} opacity-[0.07] rounded-full blur-[150px]`}
        />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-purple-500/5 rounded-full blur-[150px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen(!isSidebarOpen)} showMenu={true} />

      <main className="relative z-10 px-6 lg:px-16 py-10">
        <div className="max-w-4xl mx-auto space-y-10">
          {/* Hero */}
          <motion.section
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <p className="text-xs font-semibold tracking-[0.2em] text-gray-500 uppercase mb-3">
              Welcome back to
            </p>
            <h1 className="text-4xl md:text-5xl font-bold gradient-text leading-tight pb-1 mb-3">
              {course?.name || "…"}
            </h1>
            <div className="flex items-center gap-2 text-sm text-gray-500 mb-5">
              <Video className="w-4 h-4" />
              <span>
                {isLoading
                  ? "…"
                  : `${lectures.length} ${lectures.length === 1 ? "lecture" : "lectures"}${
                      lastUpdated ? ` · Updated ${lastUpdated}` : ""
                    }`}
              </span>
            </div>
            {/* Course summary lives in the hero, where the description used to be. */}
            {!isLoading && !isEmpty && (
              <SummaryContent state="ready" lectureCount={lectures.length} />
            )}
          </motion.section>

          {isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="glass-card rounded-2xl p-5 animate-pulse">
                  <div className="h-5 bg-white/10 rounded w-1/3 mb-3" />
                  <div className="h-3.5 bg-white/5 rounded w-full mb-2" />
                  <div className="h-3.5 bg-white/5 rounded w-3/4" />
                </div>
              ))}
            </div>
          ) : isEmpty ? (
            <EmptyCourseState courseId={courseId} />
          ) : (
            <>
              {/* Lectures */}
              <section>
                <div className="flex items-center justify-between mb-6">
                  <h2 className="text-2xl font-bold">Lectures</h2>
                  <Link to={createPageUrl(`CourseContent?courseId=${courseId}&category=media`)}>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-gray-400 hover:text-white hover:bg-white/5 rounded-full"
                    >
                      Manage lectures
                    </Button>
                  </Link>
                </div>
                <div className="relative">
                  <div className="absolute left-5 top-6 bottom-6 w-px bg-purple-500/20" />
                  <div className="space-y-3">
                    {lectures.map((lecture, index) => (
                      <LectureRow
                        key={lecture.id}
                        lecture={lecture}
                        index={index}
                        courseId={courseId}
                      />
                    ))}
                  </div>
                </div>
              </section>
            </>
          )}
        </div>
      </main>

      <CourseSidebar
        courseId={courseId}
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        activeCategory="overview"
      />
    </div>
  );
}
