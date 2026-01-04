import React, { useState } from "react";
import { getCourse } from "@/api/courses";
import { listCourseContents, createCourseContent, deleteCourseContent, getDownloadUrl } from "@/api/courseContents";
import { presignUpload, putWithProgress } from "@/api/uploads";
import { listMediaAssets, createMediaAsset, startTranscription } from "@/api/mediaAssets";
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
import { Progress } from "@/components/ui/progress";
import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";

const CATEGORY_LABELS = {
  overview: "Overview",
  media: "Course Media",
  notes: "Notes",
  past_exams: "Past Exams",
  past_assignments: "Past Assignments",
  additional_resources: "Additional Resources",
  general: "General"
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
  
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState("grid");
  const [newContent, setNewContent] = useState({ title: "", description: "", file: null });
  const [pendingUploads, setPendingUploads] = useState([]);
  const [videoPreviews, setVideoPreviews] = useState({});
  
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

  const { data: mediaAssets = [] } = useQuery({
    queryKey: ["mediaAssets", courseId],
    queryFn: () => listMediaAssets(courseId),
    enabled: !!courseId && category === "media",
    refetchInterval: (query) => {
      if (category !== "media") return false;
      const assets = Array.isArray(query?.state?.data) ? query.state.data : [];
      const hasBackendProcessing = assets.some((a) => ["queued", "processing"].includes(String(a?.status || "").toLowerCase()));
      const hasPending = pendingUploads.some((p) => ["uploading", "processing"].includes(p.stage));
      return (hasBackendProcessing || hasPending) ? 2000 : false;
    },
  });

  const isVideoFile = (file) => String(file?.type || "").toLowerCase().startsWith("video/");

  const createTempId = () =>
    `tmp_${Date.now()}_${Math.random().toString(16).slice(2)}`;

  const createContentMutation = useMutation({
    mutationFn: async (contentData) => {
      if (contentData.file) {
        setIsUploading(true);
        const isVideo = category === "media" && isVideoFile(contentData.file);
        const tempId = isVideo ? createTempId() : null;
        const previewUrl = isVideo ? URL.createObjectURL(contentData.file) : null;
        if (isVideo) {
          setPendingUploads((prev) => [
            ...prev,
            {
              tempId,
              title: contentData.title,
              description: contentData.description,
              mime_type: contentData.file.type || "video/*",
              stage: "uploading",
              progress: 0,
              previewUrl,
            },
          ]);
        }

        const presign = await presignUpload({ courseId, file: contentData.file });
        if ((presign.method || "PUT").toUpperCase() !== "PUT") {
          throw new Error(`Unsupported presign method: ${presign.method}`);
        }

        if (isVideo) {
          await putWithProgress({
            url: presign.uploadUrl,
            file: contentData.file,
            contentType: contentData.file.type || "application/octet-stream",
            onProgress: (pct) => {
              setPendingUploads((prev) =>
                prev.map((p) => (p.tempId === tempId ? { ...p, progress: pct } : p))
              );
            },
          });
        } else {
          const putRes = await fetch(presign.uploadUrl, {
            method: "PUT",
            headers: {
              "Content-Type": contentData.file.type || "application/octet-stream",
            },
            body: contentData.file,
          });
          if (!putRes.ok) {
            const text = await putRes.text().catch(() => "");
            throw new Error(`Upload failed (${putRes.status}): ${text}`);
          }
        }

        const created = await createCourseContent(courseId, {
          category,
          title: contentData.title,
          description: contentData.description,
          file_key: presign.key,
          original_filename: contentData.file.name,
          mime_type: contentData.file.type || "application/octet-stream",
          size_bytes: contentData.file.size ?? null,
        });

        if (isVideo) {
          // Move optimistic card to "processing" state linked to persisted content id.
          setPendingUploads((prev) =>
            prev.map((p) => (p.tempId === tempId ? { ...p, stage: "processing", progress: 100, contentId: created?.id } : p))
          );
          if (created?.id && previewUrl) {
            setVideoPreviews((prev) => ({ ...prev, [created.id]: previewUrl }));
          }

          // Hidden backend steps: create media asset + start transcription.
          const asset = await createMediaAsset(courseId, {
            file_key: presign.key,
            original_filename: contentData.file.name,
            mime_type: contentData.file.type || "application/octet-stream",
            size_bytes: contentData.file.size ?? null,
            content_id: created?.id ?? null,
          });
          if (asset?.id) {
            await startTranscription(asset.id);
          }
        }
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
      if (category === "media") {
        queryClient.invalidateQueries({ queryKey: ["mediaAssets", courseId] });
      }
      setIsAddDialogOpen(false);
      setNewContent({ title: "", description: "", file: null });
      setIsUploading(false);
    },
    onError: (e) => {
      // Mark any uploading optimistic cards as error.
      setPendingUploads((prev) =>
        prev.map((p) => (p.stage === "uploading" ? { ...p, stage: "error", error: String(e?.message || e || "Upload failed") } : p))
      );
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

  const contentIds = new Set((Array.isArray(content) ? content : []).map((c) => c?.id));
  const pendingVisible = pendingUploads.filter((p) => !p.contentId || !contentIds.has(p.contentId));
  const filteredPending = pendingVisible.filter((p) =>
    String(p?.title || "").toLowerCase().includes(searchQuery.toLowerCase()) ||
    String(p?.description || "").toLowerCase().includes(searchQuery.toLowerCase())
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
              Add Content
            </Button>
          </div>

          {/* Search Bar */}
          <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between mb-8">
            <div className="relative flex-1 max-w-md w-full">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
              <Input
                placeholder="Search content..."
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
                <FileText className="w-10 h-10 text-purple-400" />
              </div>
              <h3 className="text-xl font-semibold mb-2">No content yet</h3>
              <p className="text-gray-400 mb-6">Add your first {CATEGORY_LABELS[category]?.toLowerCase()} item</p>
              <Button
                onClick={() => setIsAddDialogOpen(true)}
                className="btn-gradient rounded-full px-6 py-3 h-auto font-semibold"
              >
                <Plus className="w-4 h-4 mr-2" />
                Add Content
              </Button>
            </motion.div>
          ) : (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className={viewMode === "grid" ? "grid md:grid-cols-2 lg:grid-cols-3 gap-6" : "space-y-4"}
            >
              <AnimatePresence>
                {category === "media" && filteredPending.map((p, index) => {
                  const faded = p.stage === "uploading" || p.stage === "processing";
                  const previewUrl = p.previewUrl;
                  return (
                    <motion.div
                      key={p.tempId}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.95 }}
                      transition={{ delay: index * 0.02 }}
                      className={`glass-card rounded-2xl p-6 group relative ${faded ? "opacity-60" : ""}`}
                    >
                      <div className={viewMode === "grid" ? "" : "flex items-start gap-4"}>
                        <div className={`${viewMode === "grid" ? "w-12 h-12 mb-5" : "w-12 h-12"} rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center shrink-0`}>
                          <Video className="w-6 h-6 text-purple-400" />
                        </div>
                        <div className="flex-1">
                          {previewUrl && (
                            <video
                              src={previewUrl}
                              muted
                              playsInline
                              preload="metadata"
                              className="w-full h-28 object-cover rounded-xl mb-3 border border-white/10"
                            />
                          )}
                          <h3 className="font-semibold mb-2 pr-8">{p.title}</h3>
                          {p.description && (
                            <p className="text-sm text-gray-400 line-clamp-2 mb-3">
                              {p.description}
                            </p>
                          )}

                          {p.stage === "uploading" && (
                            <div className="space-y-2">
                              <div className="text-xs text-gray-400">Uploading… {p.progress}%</div>
                              <Progress value={p.progress || 0} className="h-2 bg-white/10" />
                            </div>
                          )}
                          {p.stage === "processing" && (
                            <div className="space-y-2">
                              <div className="text-xs text-gray-400">Processing…</div>
                              <div className="animate-pulse">
                                <Progress value={100} className="h-2 bg-white/10" />
                              </div>
                            </div>
                          )}
                          {p.stage === "error" && (
                            <div className="text-xs text-red-400">
                              Failed: {p.error || "Upload failed"}
                            </div>
                          )}
                        </div>
                      </div>
                    </motion.div>
                  );
                })}

                {filteredContent.map((item, index) => {
                  const IconComponent = getFileIcon(item.mime_type);
                  const isVideo = category === "media" && String(item?.mime_type || "").toLowerCase().startsWith("video/");
                  const asset = isVideo
                    ? mediaAssets.find((a) => a?.content_id === item?.id)
                    : null;
                  const status = String(asset?.status || "").toLowerCase();
                  const isProcessing = isVideo && asset && ["queued", "processing"].includes(status);
                  const isError = isVideo && asset && status === "error";
                  const previewUrl = isVideo ? videoPreviews[item.id] : null;
                  const thumbUrl = isVideo ? asset?.thumbnail_url : null;
                  return (
                    <motion.div
                      key={item.id}
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.9 }}
                      transition={{ delay: index * 0.05 }}
                      whileHover={{ y: -3 }}
                      className={`glass-card rounded-2xl p-6 group relative ${isProcessing ? "opacity-70" : ""}`}
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
                        {isVideo && (thumbUrl || previewUrl) ? (
                          <div className={`${viewMode === "grid" ? "mb-4" : "w-40"} shrink-0`}>
                            {thumbUrl ? (
                              <img
                                src={thumbUrl}
                                alt={`${item.title} thumbnail`}
                                className={`${viewMode === "grid" ? "w-full h-28" : "w-40 h-24"} object-cover rounded-xl border border-white/10`}
                                loading="lazy"
                              />
                            ) : (
                              <video
                                src={previewUrl}
                                muted
                                playsInline
                                preload="metadata"
                                className={`${viewMode === "grid" ? "w-full h-28" : "w-40 h-24"} object-cover rounded-xl border border-white/10`}
                              />
                            )}
                          </div>
                        ) : (
                          <div className={`${viewMode === "grid" ? "w-12 h-12 mb-5" : "w-12 h-12"} rounded-xl bg-gradient-to-br from-pink-500/20 via-purple-500/20 to-blue-500/20 flex items-center justify-center shrink-0`}>
                            <IconComponent className="w-6 h-6 text-purple-400" />
                          </div>
                        )}
                        
                        <div className="flex-1">
                          <h3 className="font-semibold mb-2 pr-8">{item.title}</h3>
                          
                          {item.description && (
                            <p className="text-sm text-gray-400 line-clamp-2 mb-4">
                              {item.description}
                            </p>
                          )}
                          
                          {isVideo && isProcessing && (
                            <div className="space-y-2 mb-3">
                              <div className="text-xs text-gray-400">Processing…</div>
                              <div className="animate-pulse">
                                <Progress value={100} className="h-2 bg-white/10" />
                              </div>
                            </div>
                          )}
                          {isVideo && isError && (
                            <div className="text-xs text-red-400 mb-3">
                              Transcription failed: {asset?.transcription_error || "Unknown error"}
                            </div>
                          )}

                          {item.file_key && (
                            <button
                              type="button"
                              onClick={() => handleViewFile(item)}
                              className="inline-flex items-center gap-2 text-sm text-purple-400 hover:text-purple-300 transition-colors"
                            >
                              <File className="w-4 h-4" />
                              View File
                            </button>
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
            <DialogTitle className="text-xl font-bold">Add {CATEGORY_LABELS[category]}</DialogTitle>
            <DialogDescription className="text-gray-400">
              Add a new item to this course category. Attach a PDF to enable retrieval-augmented answers in chat.
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
              <Label className="text-gray-300 mb-2 block">Upload File</Label>
              <div className="relative">
                <input
                  type="file"
                  onChange={handleFileChange}
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
                      <span className="text-sm text-gray-400">Click to upload</span>
                    </div>
                  )}
                </label>
              </div>
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
                "Add Content"
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