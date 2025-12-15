import { request } from "./http";

export async function listCourses() {
  return request("/api/v1/courses", { method: "GET" });
}

export async function getCourse(courseId) {
  return request(`/api/v1/courses/${courseId}`, { method: "GET" });
}

export async function createCourse({ name, description }) {
  return request("/api/v1/courses", {
    method: "POST",
    body: { name, description },
  });
}

export async function deleteCourse(courseId) {
  return request(`/api/v1/courses/${courseId}`, { method: "DELETE" });
}


