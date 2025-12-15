import * as realAuth from "./auth";
import * as realCourses from "./courses";

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
    if (!raw) return { courses: [], contents: [], messages: [] };
    const parsed = JSON.parse(raw);
    return {
      courses: Array.isArray(parsed.courses) ? parsed.courses : [],
      contents: Array.isArray(parsed.contents) ? parsed.contents : [],
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
    };
  } catch {
    return { courses: [], contents: [], messages: [] };
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
    async refresh() {
      return realAuth.refresh();
    },
    async logout() {
      return realAuth.logout();
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
        const db = loadDB();
        const filtered = db.contents.filter((c) => matchesWhere(c, where));
        return sortByCreatedDate(filtered, order);
      },
      async create(data) {
        const db = loadDB();
        const item = {
          id: uid(),
          created_date: nowIso(),
          course_id: data?.course_id,
          category: data?.category,
          title: data?.title ?? "",
          description: data?.description ?? "",
          file_url: data?.file_url ?? null,
          file_type: data?.file_type ?? null,
        };
        db.contents.push(item);
        saveDB(db);
        return item;
      },
      async delete(id) {
        const db = loadDB();
        db.contents = db.contents.filter((c) => c.id !== id);
        saveDB(db);
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
      async InvokeLLM() {
        throw new Error("LLM is disabled until backend is connected.");
      },
    },
  },
};


