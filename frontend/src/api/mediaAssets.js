import { request } from "./http";

export async function listMediaAssets(courseId) {
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/media-assets`, { method: "GET" });
}

export async function createMediaAsset(courseId, payload) {
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/media-assets`, {
    method: "POST",
    body: payload,
  });
}

export async function startTranscription(mediaAssetId, { language_code } = {}) {
  return request(`/api/v1/media-assets/${encodeURIComponent(mediaAssetId)}/transcribe`, {
    method: "POST",
    body: { language_code: language_code ?? null },
  });
}

export async function getMediaAsset(mediaAssetId) {
  return request(`/api/v1/media-assets/${encodeURIComponent(mediaAssetId)}`, { method: "GET" });
}


