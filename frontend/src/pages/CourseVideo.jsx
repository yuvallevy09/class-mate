import React, { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import Navbar from "@/components/Navbar";
import CourseSidebar from "@/components/CourseSidebar";
import { getCourse } from "@/api/courses";
import {
  getBunnyEmbedUrl,
  getBunnyVideoAsset,
  listBunnyTranscriptSegments,
  listCourseVideoAssets,
  registerBunnyVideoAsset,
  reingestBunnyTranscript,
} from "@/api/videos";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/use-toast";

const TERMINAL_STATUSES = new Set(["transcript_ingested", "failed", "ingest_failed"]);

function clamp(n, min, max) {
  const x = Number(n);
  if (Number.isNaN(x)) return min;
  return Math.min(max, Math.max(min, x));
}

export default function CourseVideo() {
  const [searchParams] = useSearchParams();
  const courseId = searchParams.get("id") || searchParams.get("courseId");

  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [selectedGuid, setSelectedGuid] = useState("");
  const [jumpTo, setJumpTo] = useState("");

  // Register/update form (explicit action only).
  const [formGuid, setFormGuid] = useState("");
  const [formVideoLibraryId, setFormVideoLibraryId] = useState("");
  const [formPullZoneUrl, setFormPullZoneUrl] = useState("");
  const [formCaptionsLang, setFormCaptionsLang] = useState("en");

  // Re-ingest controls.
  const [ingestLang, setIngestLang] = useState("en");

  const queryClient = useQueryClient();

  const { data: course } = useQuery({
    queryKey: ["course", courseId],
    queryFn: () => getCourse(courseId),
    enabled: !!courseId,
  });

  const { data: assets = [] } = useQuery({
    queryKey: ["videoAssets", courseId],
    queryFn: () => listCourseVideoAssets(courseId, { provider: "bunny", limit: 50, offset: 0 }),
    enabled: !!courseId,
  });

  // Pick a default asset once list loads.
  useEffect(() => {
    if (selectedGuid) return;
    const first = assets?.[0]?.videoGuid;
    if (first) setSelectedGuid(String(first));
  }, [assets, selectedGuid]);

  // Fetch current asset details (polled with backoff until terminal state).
  const pollMsRef = useRef(5000);
  const pollTimerRef = useRef(null);

  const assetQuery = useQuery({
    queryKey: ["bunnyVideoAsset", courseId, selectedGuid],
    queryFn: () => getBunnyVideoAsset(courseId, selectedGuid),
    enabled: !!courseId && !!selectedGuid,
    retry: false,
    staleTime: 0,
  });

  useEffect(() => {
    // Stop any existing timer when guid changes.
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollMsRef.current = 5000;
  }, [selectedGuid]);

  useEffect(() => {
    if (!courseId || !selectedGuid) return;
    const asset = assetQuery.data;
    if (!asset) return;

    const status = String(asset.status || "").toLowerCase();
    const ingested = !!asset.transcriptIngestedAt;

    if (ingested || TERMINAL_STATUSES.has(status)) {
      return; // stop polling
    }

    // Poll only while not terminal. Backoff up to ~10s.
    const ms = clamp(pollMsRef.current, 5000, 10000);
    pollTimerRef.current = setTimeout(async () => {
      try {
        await assetQuery.refetch();
        pollMsRef.current = Math.min(10000, Math.floor(pollMsRef.current * 1.4));
      } catch {
        pollMsRef.current = 10000;
      }
    }, ms);

    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [assetQuery.data, courseId, selectedGuid]); // intentionally depend on data to update stop conditions

  // Prefill form when selecting an existing asset (but do NOT auto-register).
  useEffect(() => {
    const a = assetQuery.data;
    if (!a) return;
    setFormGuid(a.videoGuid || "");
    setFormVideoLibraryId(a.videoLibraryId != null ? String(a.videoLibraryId) : "");
    setFormPullZoneUrl(a.pullZoneUrl || "");
    setFormCaptionsLang(a.captionsLanguageCode || "en");
    setIngestLang(a.captionsLanguageCode || "en");
  }, [assetQuery.data]);

  const languageOptions = useMemo(() => {
    const a = assetQuery.data;
    const langs = Array.isArray(a?.availableLanguages) ? a.availableLanguages : [];
    const set = new Set(langs.filter(Boolean).map((s) => String(s)));
    if (formCaptionsLang) set.add(String(formCaptionsLang));
    if (ingestLang) set.add(String(ingestLang));
    if (!set.size) set.add("en");
    return Array.from(set);
  }, [assetQuery.data, formCaptionsLang, ingestLang]);

  const { data: segments = [] } = useQuery({
    queryKey: ["bunnySegments", courseId, selectedGuid, ingestLang],
    queryFn: () => listBunnyTranscriptSegments(courseId, selectedGuid, { languageCode: ingestLang, limit: 500, offset: 0 }),
    enabled: !!courseId && !!selectedGuid,
    retry: false,
  });

  const [iframeSrc, setIframeSrc] = useState("");

  // Initialize iframe URL from asset embedUrl once.
  useEffect(() => {
    const a = assetQuery.data;
    if (!a?.embedUrl) return;
    setIframeSrc(String(a.embedUrl));
  }, [assetQuery.data?.embedUrl]);

  const registerMutation = useMutation({
    mutationFn: async () => {
      const guid = String(formGuid || "").trim();
      const pullZoneUrl = String(formPullZoneUrl || "").trim();
      const videoLibraryId = formVideoLibraryId ? Number(formVideoLibraryId) : null;
      const captionsLanguageCode = String(formCaptionsLang || "").trim() || null;

      if (!courseId) throw new Error("courseId missing");
      if (!guid) throw new Error("videoGuid is required");
      if (!pullZoneUrl) throw new Error("pullZoneUrl is required");
      if (!videoLibraryId || Number.isNaN(videoLibraryId)) throw new Error("videoLibraryId is required");

      return registerBunnyVideoAsset(courseId, guid, {
        videoLibraryId,
        pullZoneUrl,
        captionsLanguageCode,
        contentId: null,
      });
    },
    onSuccess: (asset) => {
      toast({ title: "Saved", description: "Video asset metadata registered/updated." });
      setSelectedGuid(String(asset?.videoGuid || formGuid));
      queryClient.invalidateQueries({ queryKey: ["videoAssets", courseId] });
      queryClient.invalidateQueries({ queryKey: ["bunnyVideoAsset", courseId, String(asset?.videoGuid || formGuid)] });
    },
    onError: (e) => {
      const msg = e?.data?.detail || e?.message || "Failed to register video asset";
      toast({ title: "Register failed", description: String(msg) });
    },
  });

  const reingestMutation = useMutation({
    mutationFn: async () => {
      if (!courseId) throw new Error("courseId missing");
      if (!selectedGuid) throw new Error("Pick a video first");
      const lang = String(ingestLang || "").trim() || null;
      return reingestBunnyTranscript(courseId, selectedGuid, { languageCode: lang });
    },
    onSuccess: async () => {
      toast({ title: "Queued", description: "Re-ingest started. This may take a bit." });
      await queryClient.invalidateQueries({ queryKey: ["bunnyVideoAsset", courseId, selectedGuid] });
      pollMsRef.current = 5000; // reset backoff so UI feels responsive after clicking
    },
    onError: (e) => {
      const msg = e?.data?.detail || e?.message || "Failed to re-ingest";
      toast({ title: "Re-ingest failed", description: String(msg) });
    },
  });

  const handleJump = async (t) => {
    if (!courseId || !selectedGuid) return;
    try {
      const res = await getBunnyEmbedUrl(courseId, selectedGuid, { t });
      const url = res?.url;
      if (url) setIframeSrc(String(url));
    } catch (e) {
      const msg = e?.data?.detail || e?.message || "Failed to build embed URL";
      toast({ title: "Jump failed", description: String(msg) });
    }
  };

  const selectedAssetLabel = useMemo(() => {
    const a = assetQuery.data;
    if (!a) return selectedGuid ? `Bunny: ${selectedGuid}` : "Pick a video";
    const s = String(a.status || "");
    return `Bunny: ${a.videoGuid} (${s})`;
  }, [assetQuery.data, selectedGuid]);

  return (
    <div className="h-screen supports-[height:100dvh]:h-[100dvh] overflow-hidden flex flex-col relative">
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 right-0 w-[600px] h-[600px] bg-purple-500/5 rounded-full blur-[200px]" />
        <div className="absolute bottom-0 left-0 w-[600px] h-[600px] bg-pink-500/5 rounded-full blur-[200px]" />
      </div>

      <Navbar onMenuClick={() => setIsSidebarOpen(!isSidebarOpen)} showMenu={true} />

      <div className="flex-1 flex relative z-10 overflow-hidden min-h-0">
        <div className="flex-1 min-h-0 overflow-y-auto px-4 lg:px-8 pt-6 pb-10">
          <div className="max-w-5xl mx-auto space-y-6">
            <div className="glass-card rounded-2xl p-5">
              <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">Course Video (Debug)</h2>
                  <p className="text-xs text-gray-400">
                    {course?.name ? `Course: ${course.name}` : "Pick a course"}{" "}
                    {courseId ? <span className="text-gray-500">({courseId})</span> : null}
                  </p>
                </div>
                <div className="text-sm text-gray-300">{selectedAssetLabel}</div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="glass-card rounded-2xl p-5 space-y-4">
                <h3 className="text-sm font-semibold text-gray-200">Pick from registered videos</h3>
                <select
                  value={selectedGuid}
                  onChange={(e) => setSelectedGuid(e.target.value)}
                  className="w-full rounded-xl bg-black/30 border border-white/10 px-3 py-2 text-sm text-white"
                >
                  <option value="">Select a video…</option>
                  {assets.map((a) => (
                    <option key={a.id} value={a.videoGuid}>
                      {a.videoGuid} — {a.status}
                    </option>
                  ))}
                </select>

                <div className="text-xs text-gray-400">
                  Tip: If your video isn’t in the list yet, use the Register section below to add it.
                </div>

                <div className="rounded-xl border border-white/10 bg-black/20 p-4 space-y-2">
                  <div className="text-xs text-gray-400">Current state</div>
                  <div className="text-sm">
                    <div>
                      <span className="text-gray-400">status:</span>{" "}
                      <span className="text-white">{assetQuery.data?.status || "-"}</span>
                    </div>
                    <div>
                      <span className="text-gray-400">captionsReadyAt:</span>{" "}
                      <span className="text-white">{assetQuery.data?.captionsReadyAt || "-"}</span>
                    </div>
                    <div>
                      <span className="text-gray-400">transcriptIngestedAt:</span>{" "}
                      <span className="text-white">{assetQuery.data?.transcriptIngestedAt || "-"}</span>
                    </div>
                    <div>
                      <span className="text-gray-400">availableLanguages:</span>{" "}
                      <span className="text-white">
                        {(assetQuery.data?.availableLanguages || []).join(", ") || "-"}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="rounded-xl border border-white/10 bg-black/20 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="text-xs text-gray-400">Re-ingest captions (existing asset only)</div>
                    <Button
                      onClick={() => reingestMutation.mutate()}
                      disabled={!selectedGuid || reingestMutation.isPending}
                      className="btn-gradient rounded-xl"
                    >
                      {reingestMutation.isPending ? "Queuing..." : "Re-ingest"}
                    </Button>
                  </div>
                  <div className="flex gap-2">
                    <Input
                      value={ingestLang}
                      onChange={(e) => setIngestLang(e.target.value)}
                      placeholder="language (e.g. en)"
                      className="bg-black/30 border-white/10 text-white"
                      list="lang-options"
                    />
                    <datalist id="lang-options">
                      {languageOptions.map((l) => (
                        <option key={l} value={l} />
                      ))}
                    </datalist>
                    <Button
                      variant="secondary"
                      onClick={() => queryClient.invalidateQueries({ queryKey: ["bunnySegments", courseId, selectedGuid, ingestLang] })}
                      className="rounded-xl border-white/10 bg-white/5 hover:bg-white/10"
                      disabled={!selectedGuid}
                    >
                      Refresh segments
                    </Button>
                  </div>
                </div>
              </div>

              <div className="glass-card rounded-2xl p-5 space-y-4">
                <h3 className="text-sm font-semibold text-gray-200">Player</h3>
                {iframeSrc ? (
                  <div className="rounded-2xl overflow-hidden border border-white/10 bg-black/30">
                    <div style={{ position: "relative", paddingTop: "56.25%" }}>
                      <iframe
                        title="Bunny Stream Player"
                        src={iframeSrc}
                        loading="lazy"
                        style={{ border: "none", position: "absolute", top: 0, height: "100%", width: "100%" }}
                        allow="accelerometer; gyroscope; autoplay; encrypted-media; picture-in-picture;"
                        allowFullScreen
                      />
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-gray-400">
                    Select a registered asset (with videoLibraryId) to render the embed.
                  </div>
                )}

                <div className="flex gap-2">
                  <Input
                    value={jumpTo}
                    onChange={(e) => setJumpTo(e.target.value)}
                    placeholder='Jump to t (e.g. 50, 00:01:23, 1m30s)'
                    className="bg-black/30 border-white/10 text-white"
                  />
                  <Button
                    onClick={() => handleJump(jumpTo)}
                    disabled={!selectedGuid || !jumpTo.trim()}
                    className="btn-gradient rounded-xl"
                  >
                    Jump
                  </Button>
                </div>
              </div>
            </div>

            <div className="glass-card rounded-2xl p-5 space-y-4">
              <h3 className="text-sm font-semibold text-gray-200">Register / Update metadata (explicit)</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Input
                  value={formGuid}
                  onChange={(e) => setFormGuid(e.target.value)}
                  placeholder="video_guid"
                  className="bg-black/30 border-white/10 text-white"
                />
                <Input
                  value={formVideoLibraryId}
                  onChange={(e) => setFormVideoLibraryId(e.target.value)}
                  placeholder="video_library_id"
                  className="bg-black/30 border-white/10 text-white"
                />
                <Input
                  value={formPullZoneUrl}
                  onChange={(e) => setFormPullZoneUrl(e.target.value)}
                  placeholder="pull_zone_url (e.g. myzone)"
                  className="bg-black/30 border-white/10 text-white"
                />
                <Input
                  value={formCaptionsLang}
                  onChange={(e) => setFormCaptionsLang(e.target.value)}
                  placeholder="captions language (e.g. en)"
                  className="bg-black/30 border-white/10 text-white"
                />
              </div>
              <div className="flex gap-2">
                <Button
                  onClick={() => registerMutation.mutate()}
                  disabled={registerMutation.isPending || !courseId}
                  className="btn-gradient rounded-xl"
                >
                  {registerMutation.isPending ? "Saving..." : "Register / Update metadata"}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setFormGuid("");
                    setFormVideoLibraryId("");
                    setFormPullZoneUrl("");
                    setFormCaptionsLang("en");
                  }}
                  className="rounded-xl border-white/10 bg-white/5 hover:bg-white/10"
                >
                  Clear
                </Button>
              </div>
              <div className="text-xs text-gray-400">
                This does not run ingestion automatically. Use the Re-ingest button above.
              </div>
            </div>

            <div className="glass-card rounded-2xl p-5 space-y-4">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-gray-200">Transcript segments</h3>
                <div className="text-xs text-gray-400">
                  language: <span className="text-gray-200">{ingestLang}</span> · {segments.length} items
                </div>
              </div>

              {segments.length === 0 ? (
                <div className="text-xs text-gray-400">No segments yet. Re-ingest captions to generate them.</div>
              ) : (
                <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                  {segments.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => handleJump(Math.floor(Number(s.startSec || 0)))}
                      className="w-full text-left rounded-xl border border-white/10 bg-black/20 hover:bg-white/5 px-4 py-3 transition-colors"
                    >
                      <div className="text-xs text-gray-400">
                        {Number(s.startSec).toFixed(2)}s → {Number(s.endSec).toFixed(2)}s
                      </div>
                      <div className="text-sm text-white leading-relaxed">{s.text}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <CourseSidebar
          courseId={courseId}
          isOpen={isSidebarOpen}
          onClose={() => setIsSidebarOpen(false)}
        />
      </div>
    </div>
  );
}


