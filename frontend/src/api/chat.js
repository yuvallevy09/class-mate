import { request } from "./http";

export async function listCourseConversations(courseId) {
  if (!courseId) throw new Error("courseId is required");
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/conversations`, { method: "GET" });
}

export async function listConversationMessages(conversationId) {
  if (!conversationId) throw new Error("conversationId is required");
  return request(`/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, { method: "GET" });
}

export async function sendCourseChat({ courseId, message, conversationId = null } = {}) {
  if (!courseId) throw new Error("courseId is required");
  if (!message || !String(message).trim()) throw new Error("message is required");
  return request(`/api/v1/courses/${encodeURIComponent(courseId)}/chat`, {
    method: "POST",
    body: { message: String(message), conversationId: conversationId ?? null },
  });
}

export async function deleteConversation(conversationId) {
  if (!conversationId) throw new Error("conversationId is required");
  return request(`/api/v1/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}


