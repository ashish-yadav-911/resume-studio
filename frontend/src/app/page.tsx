"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, uploadResume } from "@/lib/api";

type Resume = { id: string; title: string; original_filename: string; active_version_id: string | null };

export default function Home() {
  const router = useRouter();
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState("");

  async function load() {
    const data = await apiFetch("/api/resumes");
    setResumes(data);
  }

  useEffect(() => { load().catch((e) => setStatus(String(e))); }, []);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setStatus("Uploading...");
    try {
      const uploaded = await uploadResume(file);
      setFile(null);
      await load();
      setStatus("Uploaded");
      if (uploaded?.resume?.id) {
        const conv = await apiFetch("/api/conversations", {
          method: "POST",
          body: JSON.stringify({ resume_id: uploaded.resume.id }),
          headers: { "Content-Type": "application/json" },
        });
        router.push(`/conversations/${conv.id}`);
      }
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function newConversation(resumeId: string) {
    const conv = await apiFetch("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ resume_id: resumeId }),
      headers: { "Content-Type": "application/json" },
    });
    router.push(`/conversations/${conv.id}`);
  }

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <section className="card p-6">
        <h2 className="text-lg font-bold mb-2 text-indigo-700">Upload Resume</h2>
        <p className="text-sm text-slate-500 mb-4">PDF or DOCX. We parse and index your experience safely.</p>
        <form onSubmit={onUpload} className="space-y-3">
          <input
            type="file"
            accept=".pdf,.docx"
            className="input w-full"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <button type="submit" className="button w-full">Upload & Start Chat</button>
        </form>
        {status && <p className="text-sm mt-3 text-slate-600">{status}</p>}
      </section>

      <section className="card p-6">
        <h2 className="text-lg font-bold mb-2 text-indigo-700">Your Resumes</h2>
        <div className="space-y-3">
          {resumes.map((r) => (
            <div key={r.id} className="flex items-center justify-between rounded-xl border border-indigo-100 bg-white p-3">
              <div>
                <div className="font-semibold">{r.title}</div>
                <div className="text-xs text-slate-500">{r.original_filename}</div>
              </div>
              <button className="button-secondary" onClick={() => newConversation(r.id)}>
                Open Chat
              </button>
            </div>
          ))}
          {resumes.length === 0 && <p className="text-sm text-slate-500">No resumes yet. Upload one to begin.</p>}
        </div>
      </section>
    </div>
  );
}