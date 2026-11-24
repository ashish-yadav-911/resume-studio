const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const BROWSER_USER_STORAGE_KEY = "resume_studio_browser_user";

function createBrowserUserId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `browser-${crypto.randomUUID()}`;
  }
  return `browser-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

export function getBrowserUserId() {
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_DEV_USER || "demo-user";
  }
  try {
    let userId = window.localStorage.getItem(BROWSER_USER_STORAGE_KEY);
    if (!userId) {
      userId = createBrowserUserId();
      window.localStorage.setItem(BROWSER_USER_STORAGE_KEY, userId);
    }
    return userId;
  } catch {
    return process.env.NEXT_PUBLIC_DEV_USER || "demo-user";
  }
}

const DEV_USER = getBrowserUserId();

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function apiUrl(path: string) {
  return `${API_BASE}${path}`;
}

export function authHeaders(): HeadersInit {
  return { "X-Dev-User": getBrowserUserId() };
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as any;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") return detail.message;
      if (typeof detail.detail === "string") return detail.detail;
    }
    if (typeof detail === "string") return detail;
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

export async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      // keep raw text
    }
    const message =
      typeof (detail as any)?.detail === "string"
        ? (detail as any).detail
        : typeof (detail as any)?.detail?.message === "string"
          ? (detail as any).detail.message
          : text || res.statusText;
    throw new ApiError(message || "Request failed", res.status, detail);
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
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      // keep raw text
    }
    const message =
      typeof (detail as any)?.detail?.message === "string"
        ? (detail as any).detail.message
        : typeof (detail as any)?.detail === "string"
          ? (detail as any).detail
          : text;
    throw new ApiError(message || "Upload failed", res.status, detail);
  }
  return res.json();
}

export async function downloadExport(resumeId: string, format: "docx" | "pdf", versionId?: string) {
  const query = new URLSearchParams({ format });
  if (versionId) query.set("version_id", versionId);
  const res = await fetch(apiUrl(`/api/resumes/${resumeId}/export?${query.toString()}`), {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new ApiError("Export failed", res.status, await res.text());
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] || `resume.${format}`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
