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

export function putWithProgress({ url, file, contentType, onProgress }) {
  if (!url) throw new Error("url is required");
  if (!file) throw new Error("file is required");

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    xhr.setRequestHeader("Content-Type", contentType || "application/octet-stream");

    xhr.upload.onprogress = (evt) => {
      if (!evt.lengthComputable) return;
      const pct = Math.round((evt.loaded / evt.total) * 100);
      onProgress?.(pct);
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Upload failed (${xhr.status}): ${xhr.responseText || ""}`));
    };
    xhr.onerror = () => reject(new Error("Upload failed (network error)"));
    xhr.send(file);
  });
}




