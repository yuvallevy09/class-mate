const DEFAULT_API_URL = "http://localhost:3001";

function joinUrl(base, path) {
  const b = base.replace(/\/+$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${b}${p}`;
}

export function getApiBaseUrl() {
  const v = import.meta?.env?.VITE_API_URL;
  return (typeof v === "string" && v.trim()) ? v.trim() : DEFAULT_API_URL;
}

export function getCookie(name) {
  if (typeof document === "undefined") return null;
  const parts = document.cookie ? document.cookie.split("; ") : [];
  for (const part of parts) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const k = part.slice(0, idx);
    if (k === name) return decodeURIComponent(part.slice(idx + 1));
  }
  return null;
}

export function getCsrfFromCookie() {
  return getCookie("csrf_token");
}

export async function csrf() {
  const url = joinUrl(getApiBaseUrl(), "/api/v1/auth/csrf");
  const res = await fetch(url, { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`CSRF failed (${res.status}): ${text}`);
  }
  return await res.json().catch(() => ({}));
}

export async function ensureCsrf() {
  // Deterministic CSRF bootstrap: if missing, mint it and re-read cookie.
  let token = getCsrfFromCookie();
  if (token) return token;
  await csrf();
  token = getCsrfFromCookie();
  if (!token) throw new Error("CSRF cookie not set after /auth/csrf");
  return token;
}

function isAuthEndpoint(path) {
  return path.startsWith("/api/v1/auth/");
}

async function parseResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return await res.json().catch(() => null);
  }
  return await res.text().catch(() => null);
}

/**
 * Minimal request helper for cookie-auth + CSRF + refresh retry.
 *
 * Notes:
 * - Always uses credentials: "include" so cookies flow.
 * - Adds X-CSRF-Token for unsafe methods.
 * - Refresh-on-401 is conservative: only for non-auth endpoints, retry once.
 */
export async function request(path, options = {}) {
  const {
    method = "GET",
    headers,
    body,
    _retried = false,
    _skipRefresh = false,
  } = options;

  const m = String(method).toUpperCase();
  const url = joinUrl(getApiBaseUrl(), path);

  const finalHeaders = new Headers(headers || {});
  const isUnsafe = ["POST", "PUT", "PATCH", "DELETE"].includes(m);
  if (isUnsafe) {
    const csrfToken = await ensureCsrf();
    finalHeaders.set("X-CSRF-Token", csrfToken);
  }

  let finalBody = body;
  if (finalBody !== undefined && finalBody !== null && typeof finalBody === "object" && !(finalBody instanceof FormData)) {
    finalHeaders.set("Content-Type", "application/json");
    finalBody = JSON.stringify(finalBody);
  }

  const res = await fetch(url, {
    method: m,
    headers: finalHeaders,
    body: finalBody,
    credentials: "include",
  });

  // Conservative refresh-on-401 retry.
  if (
    res.status === 401 &&
    !_skipRefresh &&
    !_retried &&
    !isAuthEndpoint(path)
  ) {
    const refreshRes = await fetch(joinUrl(getApiBaseUrl(), "/api/v1/auth/refresh"), {
      method: "POST",
      headers: { "X-CSRF-Token": await ensureCsrf() },
      credentials: "include",
    });
    if (refreshRes.ok) {
      return request(path, { method: m, headers: finalHeaders, body, _retried: true, _skipRefresh });
    }
  }

  const data = await parseResponse(res);
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}


