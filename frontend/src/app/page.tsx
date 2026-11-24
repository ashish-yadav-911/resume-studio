"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, errorMessage, uploadResume } from "@/lib/api";
import Sidebar, { type ConversationSummary, type ResumeSummary } from "@/components/Sidebar";
import {
  IconArrowUp,
  IconBriefcase,
  IconChat,
  IconFile,
  IconGlobe,
  IconMenu,
  IconShield,
  IconSparkles,
  IconUpload,
} from "@/components/icons";

export default function Home() {
  const router = useRouter();
  const [resumes, setResumes] = useState<ResumeSummary[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      const [resumeList, conversationList] = await Promise.all([
        apiFetch("/api/resumes"),
        apiFetch("/api/conversations"),
      ]);
      setResumes(resumeList);
      setConversations(conversationList);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  useEffect(() => {
    const notice = window.sessionStorage.getItem("resume_studio_notice");
    if (notice) {
      setStatus(notice);
      window.sessionStorage.removeItem("resume_studio_notice");
    }
    load();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setSidebarOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function startConversation(resumeId: string) {
    const created = await apiFetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_id: resumeId }),
    });
    router.push(`/conversations/${created.id}`);
  }

  async function handleFile(file: File) {
    setBusy(true);
    setError("");
    setStatus("Parsing and versioning your resume…");
    try {
      const uploaded = await uploadResume(file);
      setStatus("Resume uploaded. Opening your workspace…");
      await load();
      if (uploaded?.resume?.id) {
        await startConversation(uploaded.resume.id);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && (err.detail as any)?.existing_resume_id) {
        const existingResumeId = String((err.detail as any).existing_resume_id);
        setStatus("This resume is already in your workspace. Opening it…");
        await load();
        await startConversation(existingResumeId);
        return;
      }
      setError(errorMessage(err));
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="landing">
      <Sidebar
        resumes={resumes}
        conversations={conversations}
        activeResumeId={null}
        open={sidebarOpen}
        busy={busy}
        onClose={() => setSidebarOpen(false)}
        onNewChat={(resumeId) => {
          setSidebarOpen(false);
          if (resumeId) startConversation(resumeId).catch((err) => setError(errorMessage(err)));
          else fileRef.current?.click();
        }}
        onOpenConversation={(id) => {
          setSidebarOpen(false);
          router.push(`/conversations/${id}`);
        }}
        onSelectResume={(id) => {
          setSidebarOpen(false);
          startConversation(id).catch((err) => setError(errorMessage(err)));
        }}
        onUpload={(file) => {
          setSidebarOpen(false);
          handleFile(file);
        }}
      />

      {sidebarOpen && (
        <button className="scrim side" onClick={() => setSidebarOpen(false)} aria-label="Close menu" tabIndex={-1} />
      )}

      <main className="landing-main">
        <div className="landing-inner">
          <button className="mobile-only icon-btn" onClick={() => setSidebarOpen(true)} aria-label="Open menu" style={{ marginBottom: 16 }}>
            <IconMenu size={16} />
          </button>

          <span className="landing-kicker">
            <IconSparkles size={12} /> Evidence-grounded resume agent
          </span>
          <h1>
            A resume workspace that <span>shows its work.</span>
          </h1>
          <p className="landing-lede">
            Chat with specialist agents for company research, job-description matching, section rewrites and
            localization. Every suggestion arrives as a reviewable patch — nothing changes until you approve it.
          </p>

          <div
            className={`upload-card ${dragging ? "drag" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files?.[0];
              if (file) handleFile(file);
            }}
          >
            <span className="upload-icon">
              <IconUpload size={22} />
            </span>
            <div>
              <h3>{busy ? "Working on your resume…" : "Upload a resume to begin"}</h3>
              <p>PDF or DOCX · parsed locally · Max 10 MB · Baseline version is immutable</p>
            </div>
            <div className="upload-actions">
              <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={busy}>
                <IconUpload size={13} /> Choose file
              </button>
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx"
              className="sr-only"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) handleFile(file);
                event.target.value = "";
              }}
            />
          </div>

          {status && <div className="notice" style={{ marginTop: 10 }}>{status}</div>}
          {error && <div className="banner" style={{ marginTop: 10 }}>{error}</div>}

          <div className="section-title">
            <h2>Your resumes</h2>
            {resumes.length > 0 && <span style={{ fontSize: 11, color: "var(--muted)" }}>{resumes.length} in workspace</span>}
          </div>
          {resumes.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--ink-soft)" }}>
              No resumes yet. Upload a PDF or DOCX above — parsing handles scanned, multi-column and encrypted
              documents with explicit warnings.
            </p>
          ) : (
            <div className="resume-grid">
              {resumes.map((resume) => (
                <button
                  key={resume.id}
                  className="resume-card"
                  onClick={() => startConversation(resume.id).catch((err) => setError(errorMessage(err)))}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="feature-icon" style={{ width: 26, height: 26 }}>
                      <IconFile size={14} />
                    </span>
                    <h3>{resume.title}</h3>
                  </div>
                  <p>{resume.original_filename}</p>
                  <div className="meta">
                    <span className="chip indigo">{resume.version_count ?? 1} version(s)</span>
                    <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 3 }}>
                      Open <IconArrowUp size={11} style={{ transform: "rotate(45deg)" }} />
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}

          {conversations.length > 0 && (
            <>
              <div className="section-title">
                <h2>Recent conversations</h2>
              </div>
              <div style={{ display: "grid", gap: 6 }}>
                {conversations.slice(0, 5).map((conversation) => (
                  <button
                    key={conversation.id}
                    className="resume-card"
                    style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 12px" }}
                    onClick={() => router.push(`/conversations/${conversation.id}`)}
                  >
                    <IconChat size={15} />
                    <span style={{ flex: 1, fontSize: 12, textAlign: "left" }}>{conversation.title}</span>
                    <span style={{ fontSize: 10, color: "var(--muted)" }}>
                      {conversation.updated_at ? new Date(conversation.updated_at).toLocaleDateString() : ""}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}

          <div className="feature-row">
            <div className="feature">
              <span className="feature-icon"><IconBriefcase size={15} /></span>
              <h4>JD matching + scoring</h4>
              <p>Deterministic scoring in application code, with verbatim evidence required for every requirement.</p>
            </div>
            <div className="feature">
              <span className="feature-icon"><IconGlobe size={15} /></span>
              <h4>Localization</h4>
              <p>Translate content while validating that numbers, dates and certifications survive intact.</p>
            </div>
            <div className="feature">
              <span className="feature-icon"><IconShield size={15} /></span>
              <h4>Human approval</h4>
              <p>Accept or reject each change. Approved edits create immutable versions with undo, redo and comparison.</p>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
