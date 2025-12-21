import { request } from "./http";

export async function listCourseVideoAssets(courseId, { provider = "bunny", limit = 50, offset = 0 } = {}) {
  if (!courseId) throw new Error("courseId is required");
  const qs = new URLSearchParams();
  if (provider) qs.set("provider", String(provider));
  if (limit != null) qs.set("limit", String(limit));
  if (offset != null) qs.set("offset", String(offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/videos${suffix}`, { method: "GET" });
}

export async function getBunnyVideoAsset(courseId, videoGuid) {
  if (!courseId) throw new Error("courseId is required");
  if (!videoGuid) throw new Error("videoGuid is required");
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/videos/bunny/${encodeURIComponent(videoGuid)}`, {
    method: "GET",
  });
}

export async function registerBunnyVideoAsset(courseId, videoGuid, payload) {
  if (!courseId) throw new Error("courseId is required");
  if (!videoGuid) throw new Error("videoGuid is required");
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/videos/bunny/${encodeURIComponent(videoGuid)}/register`, {
    method: "POST",
    body: payload,
  });
}

export async function reingestBunnyTranscript(courseId, videoGuid, { languageCode = null } = {}) {
  if (!courseId) throw new Error("courseId is required");
  if (!videoGuid) throw new Error("videoGuid is required");
  return request(
    `/api/v1/courses/${encodeURIComponent(courseId)}/videos/bunny/${encodeURIComponent(videoGuid)}/transcript/reingest`,
    {
      method: "POST",
      body: { languageCode: languageCode ?? null },
    }
  );
}

export async function listBunnyTranscriptSegments(
  courseId,
  videoGuid,
  { languageCode = null, limit = 200, offset = 0 } = {}
) {
  if (!courseId) throw new Error("courseId is required");
  if (!videoGuid) throw new Error("videoGuid is required");
  const qs = new URLSearchParams();
  if (languageCode) qs.set("language_code", String(languageCode));
  if (limit != null) qs.set("limit", String(limit));
  if (offset != null) qs.set("offset", String(offset));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request(
    `/api/v1/courses/${encodeURIComponent(courseId)}/videos/bunny/${encodeURIComponent(videoGuid)}/segments${suffix}`,
    { method: "GET" }
  );
}

export async function getBunnyEmbedUrl(courseId, videoGuid, { t = null } = {}) {
  if (!courseId) throw new Error("courseId is required");
  if (!videoGuid) throw new Error("videoGuid is required");
  const qs = new URLSearchParams();
  if (t != null && String(t).trim()) qs.set("t", String(t).trim());
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request(
    `/api/v1/courses/${encodeURIComponent(courseId)}/videos/bunny/${encodeURIComponent(videoGuid)}/embed${suffix}`,
    { method: "GET" }
  );
}


