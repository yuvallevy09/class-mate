import React, { useMemo, useState } from "react";
import { getCourse } from "@/api/courses";
import { listCourseContents, createCourseContent, deleteCourseContent, getDownloadUrl } from "@/api/courseContents";
import { presignUpload } from "@/api/uploads";
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
import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";

const CATEGORY_LABELS = {
  overview: "Overview",
  media: "Videos",
  notes: "Slides & Notes",
  exams: "Exams",
  assignments: "Assignments",
  additional_resources: "Additional Resources",
};

const FILE_ICONS = {
  pdf: FileText,
  image: Image,
  video: Video,
  default: File
};

export default function CourseContent() {
  const urlParams = new URLSearchParams(window.location.search);
  const courseId = urlParams.get("courseId");
  const category = urlParams.get("category");
  const isVideosPage = category === "media";
  const videoUploadMaxSizeMb = Number(import.meta?.env?.VITE_UPLOAD_MAX_SIZE_MB) || 100;
  
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState("grid");
  const [newContent, setNewContent] = useState({ title: "", description: "", file: null });
  const [kickoffNotice, setKickoffNotice] = useState(null);
  
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
    refetchInterval: (data) => {
      const items = data || [];
      const anyProcessing = Array.isArray(items) && items.some(a => ["processing", "extracting_audio", "transcribing"].includes(a.status));
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

  const createContentMutation = useMutation({
    mutationFn: async (contentData) => {
      if (contentData.file) {
        setIsUploading(true);
        const presign = await presignUpload({ courseId, file: contentData.file });
        const putRes = await fetch(presign.uploadUrl, {
          method: presign.method || "PUT",
          headers: {
            "Content-Type": contentData.file.type || "application/octet-stream",
          },
          body: contentData.file,
        });
        if (!putRes.ok) {
          const text = await putRes.text().catch(() => "");
          throw new Error(`Upload failed (${putRes.status}): ${text}`);
        }

        const mt = (contentData.file.type || "").toLowerCase();
        if (mt.startsWith("video/")) {
          try {
            const res = await finalizeVideoUpload(courseId, {
              title: contentData.title,
              description: contentData.description,
              source_file_key: presign.key,
              original_filename: contentData.file.name,
              mime_type: contentData.file.type || "application/octet-stream",
              size_bytes: contentData.file.size ?? null,
              kickoffTranscription: false,
            });
            const videoAsset = res?.videoAsset;
            // Fire-and-forget UX: if transcription fails, the asset will be marked "error" and can be retried later.
            if (videoAsset?.id) {
              transcribeVideoAsset(videoAsset.id, { force: false }).catch((e) => {
                console.error("Video transcription kickoff failed:", e);
                setKickoffNotice({
                  type: "error",
                  message:
                    "Upload succeeded, but transcription didn’t start. Please check your config, or click Retry on the video card.",
                });
                window.setTimeout(() => setKickoffNotice(null), 8000);
              });
            }
          } catch (e) {
            console.error("Video transcription kickoff failed:", e);
            setKickoffNotice({
              type: "error",
              message:
                "Upload succeeded, but we couldn’t register the video for transcription. Please refresh and try again.",
            });
            window.setTimeout(() => setKickoffNotice(null), 8000);
          }
          return;
        }

        await createCourseContent(courseId, {
          category,
          title: contentData.title,
          description: contentData.description,
          file_key: presign.key,
          original_filename: contentData.file.name,
          mime_type: contentData.file.type || "application/octet-stream",
          size_bytes: contentData.file.size ?? null,
        });
        return;
      }

      await createCourseContent(courseId, {
        category,
        title: contentData.title,
        description: contentData.description,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['content', courseId, category] });
      setIsAddDialogOpen(false);
      setNewContent({ title: "", description: "", file: null });
      setIsUploading(false);
    },
    onError: () => {
      setIsUploading(false);
    }
  });

  const deleteContentMutation = useMutation({
    mutationFn: (contentId) => deleteCourseContent(contentId),
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
    if (newContent.title.trim()) {
      createContentMutation.mutate(newContent);
    }
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
      const res = await getDownloadUrl(item.id);
      if (res?.url) window.open(res.url, "_blank", "noopener,noreferrer");
    } catch (e) {
      console.error(e);
    }
  };

  const filteredContent = content.filter(item =>
    item.title?.toLowerCase().includes(searchQuery.toLowerCase()) ||
    item.description?.toLowerCase().includes(searchQuery.toLowerCase())
  );

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
          ) : content.length === 0 ? (
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
                {filteredContent.map((item, index) => {
                  const IconComponent = getFileIcon(item.mime_type);
                  const mt = (item.mime_type || "").toLowerCase();
                  const isVideo = mt.startsWith("video/");
                  const asset = isVideo ? (videoAssetByContentId.get(item.id) || null) : null;
                  const stage = asset?.status || null;
                  const isProcessing = stage === "processing" || stage === "extracting_audio" || stage === "transcribing";
                  const isError = stage === "error";
                  const stageLabel =
                    stage === "processing" ? "Starting" :
                    stage === "extracting_audio" ? "Extracting audio" :
                    stage === "transcribing" ? "Transcribing" :
                    null;
                  const stageProgress =
                    stage === "processing" ? 15 :
                    stage === "extracting_audio" ? 45 :
                    stage === "transcribing" ? 85 :
                    0;
                  return (
                    <motion.div
                      key={item.id}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.9 }}
                      transition={{ delay: index * 0.05 }}
                      whileHover={{ y: -3 }}
                      className={`glass-card rounded-2xl p-6 group relative ${isProcessing ? "opacity-60" : ""} ${isError ? "opacity-75" : ""}`}
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
                        <div className={`${viewMode === "grid" ? "w-12 h-12 mb-5" : "w-12 h-12"} rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center shrink-0`}>
                          <IconComponent className="w-6 h-6 text-purple-400" />
                        </div>
                        
                        <div className="flex-1">
                          <h3 className="font-semibold mb-2 pr-8">{item.title}</h3>
                          
                          {item.description && (
                            <p className="text-sm text-gray-400 line-clamp-2 mb-4">
                              {item.description}
                            </p>
                          )}
                          
                          {item.file_key && (
                            <button
                              type="button"
                              onClick={() => handleViewFile(item)}
                              className="inline-flex items-center gap-2 text-sm text-purple-400 hover:text-purple-300 transition-colors"
                            >
                              {isVideo ? <Video className="w-4 h-4" /> : <File className="w-4 h-4" />}
                              {isVideo ? "View Video" : "View File"}
                            </button>
                          )}

                          {isVideo && isProcessing && (
                            <div className="mt-4">
                              <div className="flex items-center justify-between text-xs text-gray-300 mb-2">
                                <span>{stageLabel}</span>
                                <span>{stageProgress}%</span>
                              </div>
                              <div className="h-2 w-full rounded-full bg-white/10 overflow-hidden">
                                <div
                                  className="h-full bg-purple-500/70"
                                  style={{ width: `${stageProgress}%` }}
                                />
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
                    Max size: <span className="text-gray-200">{videoUploadMaxSizeMb}MB</span>.
                  </div>
                </div>
              )}
            </div>
            <Button
              onClick={handleCreate}
              disabled={!newContent.title.trim() || createContentMutation.isPending}
              className="w-full btn-gradient rounded-xl h-12 font-semibold"
            >
              {createContentMutation.isPending ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {isUploading ? "Uploading..." : "Creating..."}
                </span>
              ) : (
                isVideosPage ? "Add Video" : "Add Content"
              )}
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