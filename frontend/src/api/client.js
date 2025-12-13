const DB_KEY = "classmate_db_v1";
const USER_KEY = "classmate_user_v1";

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

function ensureUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // ignore
  }
  const user = { id: uid(), full_name: "You", email: "you@example.com" };
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  return user;
}

export const client = {
  auth: {
    async me() {
      return ensureUser();
    },
    logout() {
      localStorage.removeItem(USER_KEY);
      window.location.assign("/");
    },
  },

  entities: {
    Course: {
      async list(order) {
        const db = loadDB();
        return sortByCreatedDate(db.courses, order);
      },
      async filter(where, order) {
        const db = loadDB();
        const filtered = db.courses.filter((c) => matchesWhere(c, where));
        return sortByCreatedDate(filtered, order);
      },
      async create(data) {
        const db = loadDB();
        const course = {
          id: uid(),
          created_date: nowIso(),
          name: data?.name ?? "",
          description: data?.description ?? "",
          instructor: data?.instructor,
          color: data?.color,
        };
        db.courses.push(course);
        saveDB(db);
        return course;
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


