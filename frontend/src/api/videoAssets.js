import { request } from "./http";

export async function listVideoAssets(courseId) {
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/video-assets`, { method: "GET" });
}

export async function createVideoAsset(courseId, payload) {
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/video-assets`, {
    method: "POST",
    body: payload,
  });
}

export async function transcribeVideoAsset(videoAssetId, payload = {}) {
  return request(`/api/v1/video-assets/${encodeURIComponent(videoAssetId)}/transcribe`, {
    method: "POST",
    body: payload,
  });
}


