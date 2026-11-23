"use client";

import { useEffect, useState } from "react";
import { apiFetch, uploadResume } from "@/lib/api";

type Resume = { id: string; title: string; original_filename: string; active_version_id: string | null };

export default function Home() {
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
      await uploadResume(file);
      setFile(null);
      await load();
      setStatus("Uploaded");
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
    window.location.href = `/conversations/${conv.id}`;
  }

  return (
    <div className="space-y-6">
      <section className="bw-card p-4">
        <h2 className="text-lg font-bold mb-3">Upload Resume</h2>
        <form onSubmit={onUpload} className="flex gap-3 items-center">
          <input
            type="file"
            accept=".pdf,.docx"
            className="bw-input px-3 py-2"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <button type="submit" className="bw-button px-4 py-2">Upload</button>
        </form>
        {status && <p className="text-sm mt-2">{status}</p>}
      </section>

      <section className="bw-card p-4">
        <h2 className="text-lg font-bold mb-3">Your Resumes</h2>
        <div className="space-y-2">
          {resumes.map((r) => (
            <div key={r.id} className="flex items-center justify-between border border-black p-3">
              <div>
                <div className="font-bold">{r.title}</div>
                <div className="text-sm">{r.original_filename}</div>
              </div>
              <button className="bw-button px-3 py-1" onClick={() => newConversation(r.id)}>
                Open Chat
              </button>
            </div>
          ))}
          {resumes.length === 0 && <p className="text-sm">No resumes yet.</p>}
        </div>
      </section>
    </div>
  );
}