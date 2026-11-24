"use client";

import { useRef } from "react";
import {
  IconChat,
  IconFile,
  IconPlus,
  IconUpload,
  IconX,
} from "./icons";

export type ResumeSummary = {
  id: string;
  title: string;
  original_filename: string;
  active_version_id: string | null;
  version_count?: number;
};

export type ConversationSummary = {
  id: string;
  title: string;
  resume_id: string | null;
  updated_at?: string;
};

type Props = {
  resumes: ResumeSummary[];
  conversations: ConversationSummary[];
  activeConversationId?: string | null;
  activeResumeId?: string | null;
  open?: boolean;
  busy?: boolean;
  onClose?: () => void;
  onNewChat: (resumeId?: string) => void;
  onOpenConversation: (id: string) => void;
  onSelectResume: (id: string) => void;
  onUpload: (file: File) => void;
};

export default function Sidebar({
  resumes,
  conversations,
  activeConversationId,
  activeResumeId,
  open,
  busy,
  onClose,
  onNewChat,
  onOpenConversation,
  onSelectResume,
  onUpload,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Workspace navigation">
      <div className="sidebar-head">
        <div className="brand">
          <span className="brand-mark">R</span>
          <span>
            Resume Studio
            <small>Evidence-grounded AI</small>
          </span>
          <button className="mobile-only icon-btn" style={{ marginLeft: "auto", width: 30, height: 30 }} onClick={onClose} aria-label="Close menu">
            <IconX size={15} />
          </button>
        </div>
      </div>

      <div style={{ padding: "0 12px 10px" }}>
        <button className="side-btn primary" onClick={() => onNewChat(activeResumeId || resumes[0]?.id)} disabled={busy || resumes.length === 0}>
          <IconPlus size={15} /> New chat
        </button>
        <button className="side-btn" style={{ marginTop: 6 }} onClick={() => fileRef.current?.click()} disabled={busy}>
          <IconUpload size={15} /> Upload resume
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx"
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
            event.target.value = "";
          }}
        />
      </div>

      <div className="sidebar-body">
        <div className="sidebar-section" style={{ marginTop: 4 }}>
          <div className="sidebar-label">Resumes</div>
          {resumes.map((resume) => (
            <button
              key={resume.id}
              className={`sidebar-item ${resume.id === activeResumeId ? "active" : ""}`}
              onClick={() => onSelectResume(resume.id)}
              title={resume.title}
            >
              <IconFile className="sidebar-icon" />
              <span className="truncate" style={{ flex: 1 }}>{resume.title}</span>
              {typeof resume.version_count === "number" && (
                <span style={{ fontSize: 10, color: "#8792b4" }}>v{resume.version_count}</span>
              )}
            </button>
          ))}
          {resumes.length === 0 && (
            <p style={{ padding: "4px 8px", fontSize: 11, color: "#7f8aad" }}>No resumes yet.</p>
          )}
        </div>

        <div className="sidebar-section">
          <div className="sidebar-label">Conversations</div>
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              className={`sidebar-item ${conversation.id === activeConversationId ? "active" : ""}`}
              onClick={() => onOpenConversation(conversation.id)}
              title={conversation.title}
            >
              <IconChat className="sidebar-icon" />
              <span className="truncate" style={{ flex: 1 }}>{conversation.title}</span>
            </button>
          ))}
          {conversations.length === 0 && (
            <p style={{ padding: "4px 8px", fontSize: 11, color: "#7f8aad" }}>No conversations yet.</p>
          )}
        </div>
      </div>

      <div className="sidebar-foot">
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 4px 8px" }}>
          <span className="brand-mark" style={{ width: 26, height: 26, fontSize: 11 }}>D</span>
          <span style={{ minWidth: 0 }}>
            <span style={{ display: "block", fontSize: 11.5, fontWeight: 600, color: "#e7e9f4" }}>Demo workspace</span>
            <span style={{ display: "block", fontSize: 10, color: "#7f8aad" }}>Local dev mode</span>
          </span>
        </div>
        <p style={{ margin: 0, padding: "0 4px", fontSize: 9.5, lineHeight: 1.45, color: "#69759a" }}>
          AI proposes, you decide. Every change needs approval before it becomes a new version.
        </p>
      </div>
    </aside>
  );
}
