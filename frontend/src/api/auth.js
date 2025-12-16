import { request, ensureCsrf, csrf as csrfCall } from "./http";

export async function csrf() {
  return csrfCall();
}

export async function login(email, password) {
  await ensureCsrf();
  return request("/api/v1/auth/login", {
    method: "POST",
    body: { email, password },
    _skipRefresh: true,
  });
}

export async function signup(email, password, displayName) {
  await ensureCsrf();
  return request("/api/v1/auth/signup", {
    method: "POST",
    body: { email, password, displayName },
    _skipRefresh: true,
  });
}

export async function logout() {
  await ensureCsrf();
  return request("/api/v1/auth/logout", {
    method: "POST",
    _skipRefresh: true,
  });
}

export async function refresh() {
  await ensureCsrf();
  return request("/api/v1/auth/refresh", {
    method: "POST",
    _skipRefresh: true,
  });
}

export async function me() {
  try {
    return await request("/api/v1/users/me", {
      method: "GET",
    });
  } catch (e) {
    if (e?.status === 401) return null;
    throw e;
  }
}


