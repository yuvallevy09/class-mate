import { request } from "./http";

export async function listCourseConversations(courseId, { videoAssetId } = {}) {
  if (!courseId) throw new Error("courseId is required");
  const qs = videoAssetId ? `?video_asset_id=${encodeURIComponent(videoAssetId)}` : "";
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/conversations${qs}`, { method: "GET" });
}

export async function listConversationMessages(conversationId) {
  if (!conversationId) throw new Error("conversationId is required");
  return request(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, { method: "GET" });
}

export async function sendCourseChat({
  courseId,
  message,
  conversationId = null,
  watchingVideoAssetId = null,
  watchingTimestampSec = null,
} = {}) {
  if (!courseId) throw new Error("courseId is required");
  if (!message || !String(message).trim()) throw new Error("message is required");
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/chat-v2`, {
    method: "POST",
    body: {
      message: String(message),
      conversationId: conversationId ?? null,
      // Video-player viewing context (null for course chat). Presence of an
      // asset id puts the backend in video mode (scoped retrieval + anchor).
      watchingVideoAssetId: watchingVideoAssetId ?? null,
      watchingTimestampSec: watchingTimestampSec ?? null,
    },
  });
}

export async function deleteConversation(conversationId) {
  if (!conversationId) throw new Error("conversationId is required");
  return request(`/api/v1/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}


