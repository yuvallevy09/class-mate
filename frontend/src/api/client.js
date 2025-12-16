import * as realAuth from "./auth";
import * as realCourses from "./courses";
import * as realCourseContents from "./courseContents";
import { request } from "./http";

const DB_KEY = "classmate_db_v1";

function uid() {
  return globalThis.crypto?.randomUUID?.() ?? `id_${Math.random().toString(16).slice(2)}_${Date.now()}`;
}

function nowIso() {
  return new Date().toISOString();
}

function loadDB() {
  try {
    const raw = localStorage.getItem(DB_KEY);
    if (!raw) return { messages: [] };
    const parsed = JSON.parse(raw);
    return {
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
    };
  } catch {
    return { messages: [] };
  }
}

function saveDB(db) {
  localStorage.setItem(DB_KEY, JSON.stringify(db));
}

function sortByCreatedDate(items, order) {
  if (order === "-created_date") {
    return [...items].sort((a, b) => (b.created_date || "").localeCompare(a.created_date || ""));
  }
  if (order === "created_date") {
    return [...items].sort((a, b) => (a.created_date || "").localeCompare(b.created_date || ""));
  }
  return items;
}

function matchesWhere(item, where = {}) {
  if (!where || typeof where !== "object") return true;
  for (const [k, v] of Object.entries(where)) {
    if (v === undefined || v === null || v === "") continue;
    if (item?.[k] !== v) return false;
  }
  return true;
}

export const client = {
  auth: {
    async me() {
      return realAuth.me();
    },
    async csrf() {
      return realAuth.csrf();
    },
    async login({ email, password }) {
      return realAuth.login(email, password);
    },
    async signup({ displayName, email, password }) {
      return realAuth.signup(email, password, displayName);
    },
    async refresh() {
      return realAuth.refresh();
    },
    async logout() {
      return realAuth.logout();
    },
    async deleteMe() {
      return realAuth.deleteMe();
    },
  },

  entities: {
    Course: {
      async list(order) {
        const courses = await realCourses.listCourses();
        const normalized = (Array.isArray(courses) ? courses : []).map((c) => ({
          ...c,
          // Keep legacy field name used by the UI/local DB.
          created_date: c.created_at,
        }));
        return sortByCreatedDate(normalized, order);
      },
      async filter(where, order) {
        if (where?.id) {
          const course = await realCourses.getCourse(where.id);
          const normalized = { ...course, created_date: course.created_at };
          return [normalized];
        }
        const all = await this.list(order);
        const filtered = all.filter((c) => matchesWhere(c, where));
        return sortByCreatedDate(filtered, order);
      },
      async create(data) {
        const created = await realCourses.createCourse({
          name: data?.name ?? "",
          description: data?.description ?? "",
        });
        return { ...created, created_date: created.created_at };
      },
      async delete(id) {
        return realCourses.deleteCourse(id);
      },
    },

    CourseContent: {
      async filter(where, order) {
        const courseId = where?.course_id;
        if (!courseId) return [];

        const items = await realCourseContents.listCourseContents(courseId, { category: where?.category });
        const normalized = (Array.isArray(items) ? items : []).map((c) => ({
          ...c,
          created_date: c.created_at,
        }));
        const filtered = normalized.filter((c) => matchesWhere(c, where));
        return sortByCreatedDate(filtered, order);
      },
      async create(data) {
        const courseId = data?.course_id;
        if (!courseId) throw new Error("course_id is required");

        const created = await realCourseContents.createCourseContent(courseId, {
          category: data?.category,
          title: data?.title ?? "",
          description: data?.description ?? null,
          // Keep backend-compatible file fields (legacy UI fields are ignored by backend).
          file_key: data?.file_key ?? null,
          original_filename: data?.original_filename ?? null,
          mime_type: data?.mime_type ?? null,
          size_bytes: data?.size_bytes ?? null,
        });
        return { ...created, created_date: created?.created_at };
      },
      async delete(id) {
        await realCourseContents.deleteCourseContent(id);
        return { ok: true };
      },
    },

    ChatMessage: {
      async filter(where, order) {
        const db = loadDB();
        const filtered = db.messages.filter((m) => matchesWhere(m, where));
        return sortByCreatedDate(filtered, order);
      },
      async create(data) {
        const db = loadDB();
        const msg = {
          id: uid(),
          created_date: nowIso(),
          course_id: data?.course_id,
          role: data?.role,
          content: data?.content ?? "",
        };
        db.messages.push(msg);
        saveDB(db);
        return msg;
      },
    },
  },

  integrations: {
    Core: {
      async UploadFile({ file }) {
        if (!file) throw new Error("No file provided");
        const file_url = URL.createObjectURL(file);
        return { file_url };
      },
      async InvokeLLM({ courseId, message, conversationId } = {}) {
        if (!courseId) throw new Error("courseId is required");
        if (!message || !String(message).trim()) throw new Error("message is required");

        return request(`/api/v1/courses/${encodeURIComponent(courseId)}/chat`, {
          method: "POST",
          body: {
            message: String(message),
            conversationId: conversationId ?? null,
          },
        });
      },
    },
  },
};


