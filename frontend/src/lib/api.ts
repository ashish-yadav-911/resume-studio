const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const DEV_USER = process.env.NEXT_PUBLIC_DEV_USER || "demo-user";

export function apiUrl(path: string) {
  return `${API_BASE}${path}`;
}

export function authHeaders(): HeadersInit {
  return { "X-Dev-User": DEV_USER };
}

export async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export async function uploadResume(file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(apiUrl("/api/resumes/upload"), {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}