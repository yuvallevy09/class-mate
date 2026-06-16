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

// PUT a file to a presigned URL while reporting upload progress.
// Uses XMLHttpRequest because fetch() can't surface upload progress events.
export function putFileWithProgress({ url, method = "PUT", file, onProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method || "PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && typeof onProgress === "function") {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Upload failed (${xhr.status}): ${xhr.responseText || ""}`));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed (network error)"));
    xhr.send(file);
  });
}


