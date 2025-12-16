import { request } from "./http";

export async function presignUpload({ courseId, file }) {
  if (!courseId) throw new Error("courseId is required");
  if (!file) throw new Error("file is required");

  return request("/api/v1/uploads/presign", {
    method: "POST",
    body: {
      courseId,
      filename: file.name,
      contentType: file.type || "application/octet-stream",
      sizeBytes: file.size ?? 0,
    },
  });
}


