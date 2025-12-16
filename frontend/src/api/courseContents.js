import { request } from "./http";

export async function listCourseContents(courseId, { category } = {}) {
  const qs = category ? `?category=${encodeURIComponent(category)}` : "";
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/contents${qs}`, { method: "GET" });
}

export async function createCourseContent(courseId, payload) {
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/contents`, {
    method: "POST",
    body: payload,
  });
}

export async function deleteCourseContent(contentId) {
  // Backend returns 204; request() will resolve with empty text -> null-ish.
  return request(`/api/v1/contents/${encodeURIComponent(contentId)}`, { method: "DELETE" });
}

export async function getDownloadUrl(contentId) {
  return request(`/api/v1/contents/${encodeURIComponent(contentId)}/download`, { method: "GET" });
}


