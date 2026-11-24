"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  apiFetch,
  apiUrl,
  authHeaders,
  downloadExport,
  errorMessage,
  uploadResume,
} from "@/lib/api";
import Sidebar, { type ConversationSummary, type ResumeSummary } from "@/components/Sidebar";
import ResumeDocument, { type HighlightMap } from "@/components/ResumeDocument";
import SimpleMarkdown from "@/components/SimpleMarkdown";
import {
  IconArrowUp,
  IconBriefcase,
  IconCheck,
  IconDownload,
  IconFile,
  IconMenu,
  IconRefresh,
  IconShield,
  IconSparkles,
  IconUndo,
  IconRedo,
  IconX,
} from "@/components/icons";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */
type Message = {
  id: string;
  role: string;
  content: string;
  run_id?: string | null;
  meta?: Record<string, any>;
  created_at?: string;
};

type Version = {
  id: string;
  version_number: number;
  change_summary?: string;
  created_at?: string;
  is_active?: boolean;
  kind?: string;
  content?: any;
};

type Change = {
  id: string;
  field_path: string;
  section?: string;
  before?: string;
  after?: string;
  operation?: string;
  requires_user_confirmation?: boolean;
  reason?: string;
  status: string;
  agent?: string;
};

type Proposal = {
  id: string;
  summary?: string;
  status: string;
  changes: Change[];
  candidate_content?: any;
  validation?: { warnings?: string[]; unsupported_claims?: string[]; errors?: string[] };
};

type RunPayload = {
  id: string;
  status: string;
  intent?: string;
  final_response?: string;
  error?: string;
  proposal?: Proposal | null;
  agent_outputs?: Record<string, any>;
  plan?: Record<string, any>;
};

type StreamEvent = {
  type: string;
  payload?: Record<string, any>;
  created_at?: string;
};

type RightTab = "resume" | "changes" | "history";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */
const AGENT_LABELS: Record<string, string> = {
  company_research: "Company research",
  job_matching: "JD matching",
  section_enhancement: "Section enhancement",
  translation: "Translation",
};

const INTENT_LABELS: Record<string, string> = {
  COMPANY_OPTIMIZATION: "Company optimization",
  JOB_MATCHING: "Job matching",
  SECTION_ENHANCEMENT: "Section enhancement",
  TRANSLATION: "Translation",
  RESUME_ANALYSIS: "Resume analysis",
  GENERAL_CHAT: "Assistant",
  RESUME_EXPORT: "Export",
  VERSION_COMPARISON: "Version comparison",
  UNDO: "Undo",
  REDO: "Redo",
  PENDING: "Routing",
};

function prettyPath(path?: string) {
  if (!path) return "Resume field";
  return path
    .split(".")
    .map((segment) => (/^\d+$/.test(segment) ? `#${Number(segment) + 1}` : segment.replace(/_/g, " ")))
    .join(" › ");
}

function timeOf(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function describeEvent(event: StreamEvent) {
  const payload = event.payload || {};
  if (payload.message) return String(payload.message);
  if (payload.summary) return String(payload.summary);
  switch (event.type) {
    case "intent.classified":
      return `Intent: ${INTENT_LABELS[payload.intent] || payload.intent || "routing"}`;
    case "agent.started":
      return `${AGENT_LABELS[payload.agent] || payload.agent} started`;
    case "agent.completed":
      return `${AGENT_LABELS[payload.agent] || payload.agent} finished`;
    case "agent.failed":
      return `${AGENT_LABELS[payload.agent] || payload.agent} failed safely`;
    case "validation.completed":
      return `Validation passed; ${payload.blocked ?? 0} change(s) blocked`;
    case "proposal.created":
      return `${payload.change_count ?? 0} change(s) ready for review`;
    case "security.notice":
      return "Instruction-like text treated as data";
    case "run.awaiting_approval":
      return "Proposal ready for your approval";
    case "run.completed":
      return "Run complete";
    case "run.failed":
      return "Run failed safely";
    default:
      return event.type;
  }
}

function StatusPill({ status, streaming }: { status?: string; streaming: boolean }) {
  if (streaming) {
    return (
      <span className="chip amber">
        <IconRefresh size={11} className="spin" /> Working
      </span>
    );
  }
  const map: Record<string, { label: string; tone: string }> = {
    awaiting_approval: { label: "Awaiting approval", tone: "amber" },
    approved: { label: "Approved", tone: "green" },
    rejected: { label: "Rejected", tone: "slate" },
    failed: { label: "Failed", tone: "rose" },
    clarification: { label: "Needs input", tone: "amber" },
    completed: { label: "Ready", tone: "green" },
    running: { label: "Working", tone: "amber" },
  };
  const item = (status && map[status]) || { label: "Ready", tone: "slate" };
  return <span className={`chip ${item.tone}`}>{item.label}</span>;
}

/* ------------------------------------------------------------------ */
/* Workspace                                                           */
/* ------------------------------------------------------------------ */
function Workspace() {
  const params = useParams();
  const router = useRouter();
  const conversationId = (Array.isArray(params?.id) ? params.id[0] : params?.id) as string;

  const [resumes, setResumes] = useState<ResumeSummary[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<any>(null);
  const [resume, setResume] = useState<any>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);

  const [input, setInput] = useState("");
  const [jd, setJd] = useState("");
  const [showJd, setShowJd] = useState(false);
  const [jdAttached, setJdAttached] = useState(false);

  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunPayload | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [busy, setBusy] = useState(false);

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [rightTab, setRightTab] = useState<RightTab>("resume");
  const [previewMode, setPreviewMode] = useState<"base" | "proposal">("base");
  const [previewVersion, setPreviewVersion] = useState<Version | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);

  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [compare, setCompare] = useState<any>(null);

  const streamRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const resumeId = conversation?.resume_id || resume?.id || null;

  /* ------------------------------ loading ------------------------------ */
  async function loadWorkspace() {
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

  async function loadMessages(id = conversationId) {
    if (!id) return [];
    const list = await apiFetch(`/api/conversations/${id}/messages`);
    setMessages(list);
    return list as Message[];
  }

  /**
   * Reloading a conversation must restore an unresolved proposal. The assistant
   * message keeps the run id and status, so fetch the latest run once and reopen
   * the review state instead of silently losing the proposal on refresh.
   */
  async function restorePendingProposal(list: Message[]) {
    const pending = [...list]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          message.run_id &&
          message.meta?.status === "awaiting_approval"
      );
    if (!pending?.run_id) return;
    try {
      const payload: RunPayload = await apiFetch(`/api/runs/${pending.run_id}`);
      if (!payload.proposal || payload.proposal.status !== "pending") return;
      setRunId(pending.run_id);
      setRun(payload);
      setProposal(payload.proposal);
      setSelected(
        payload.proposal.changes.filter((change) => change.status === "proposed").map((change) => change.id)
      );
      setRightTab("changes");
      setPreviewMode("proposal");
    } catch {
      // A stale run should not block the workspace from loading.
    }
  }

  async function loadResume(id: string | null | undefined) {
    if (!id) {
      setResume(null);
      setVersions([]);
      return;
    }
    try {
      const [details, versionList] = await Promise.all([
        apiFetch(`/api/resumes/${id}`),
        apiFetch(`/api/resumes/${id}/versions`),
      ]);
      setResume(details);
      setVersions(versionList.versions || []);
      setCanUndo(Boolean(versionList.can_undo));
      setCanRedo(Boolean(versionList.can_redo));
    } catch (err) {
      const message = errorMessage(err);
      if (/not found/i.test(message)) {
        setResume(null);
        setVersions([]);
        return;
      }
      setError(message);
    }
  }

  async function loadConversation() {
    if (!conversationId) return;
    try {
      const conversationData = await apiFetch(`/api/conversations/${conversationId}`);
      setConversation(conversationData);
      const [list] = await Promise.all([
        loadMessages(conversationId),
        loadResume(conversationData.resume_id),
      ]);
      await restorePendingProposal(list);
    } catch (err) {
      const message = errorMessage(err);
      if (/not found/i.test(message)) {
        window.sessionStorage.setItem(
          "resume_studio_notice",
          "Your previous session is no longer available. Upload or open a resume to continue."
        );
        router.replace("/");
        return;
      }
      setError(message);
    }
  }

  useEffect(() => {
    loadWorkspace();
    loadConversation();
    return () => {
      streamRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streaming, events]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSidebarOpen(false);
        setViewerOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  /* ------------------------------ streaming ---------------------------- */
  async function finishRun(id: string) {
    const payload: RunPayload = await apiFetch(`/api/runs/${id}`);
    setRun(payload);
    const nextProposal = payload.proposal || null;
    setProposal(nextProposal);
    const missingSkills = payload.agent_outputs?.matching?.missing_skills;
    if (Array.isArray(missingSkills) && missingSkills.length > 0 && !nextProposal) {
      setNotice(`Skills the job asks for but not evidenced in your resume: ${missingSkills.slice(0, 8).join(", ")}. Add real experience with these before tailoring.`);
    }
    if (nextProposal) {
      setSelected(nextProposal.changes.filter((c) => c.status === "proposed").map((c) => c.id));
      setRightTab("changes");
      setPreviewMode("proposal");
      setViewerOpen(true);
    } else {
      setRightTab("resume");
      setPreviewMode("base");
    }
    await Promise.all([loadMessages(conversationId), loadResume(resumeId)]);
    await loadWorkspace();
  }

  /**
   * Serverless hosts freeze anything that outlives the HTTP response, so a queued run
   * has to be executed by a request. This posts /execute; the SSE stream below replays
   * the persisted events, so nothing is lost if this call is slow.
   */
  async function executeRun(id: string) {
    try {
      await apiFetch(`/api/runs/${id}/execute`, { method: "POST" });
    } catch {
      // The stream still surfaces whatever failure the run persisted.
    }
  }

  async function streamRun(id: string) {
    streamRef.current?.abort();
    const controller = new AbortController();
    streamRef.current = controller;
    setStreaming(true);
    setEvents([]);
    setError("");

    try {
      const response = await fetch(apiUrl(`/api/runs/${id}/events`), {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`Could not subscribe to run events (${response.status}).`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let terminal = false;

      while (!terminal) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          let data = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (data) {
            try {
              const event = JSON.parse(data) as StreamEvent;
              setEvents((prev) => [...prev, event]);
              if (["run.completed", "run.failed", "run.awaiting_approval", "run.clarification", "run.rejected"].includes(event.type)) {
                terminal = true;
                break;
              }
            } catch {
              // Ignore malformed keep-alive frames.
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (terminal) {
          await reader.cancel().catch(() => undefined);
        }
      }
      await finishRun(id);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err));
    } finally {
      setStreaming(false);
      setBusy(false);
    }
  }

  /* ------------------------------ actions ------------------------------ */
  async function send() {
    const content = input.trim();
    if (!content || streaming || busy) return;
    const attachedJd = jd.trim();
    const optimistic: Message = { id: `local-${Date.now()}`, role: "user", content };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    setError("");
    setNotice("");
    setProposal(null);
    setRun(null);
    setSelected([]);
    setPreviewMode("base");
    setPreviewVersion(null);
    setRightTab("resume");
    setEvents([]);
    setBusy(true);

    try {
      const response = await apiFetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          job_description: attachedJd || undefined,
        }),
      });
      if (attachedJd) {
        setJdAttached(true);
        setJd("");
        setShowJd(false);
      }
      setMessages((prev) => prev.map((message) => (message.id === optimistic.id ? response.message : message)));
      setRunId(response.run_id);
      void executeRun(response.run_id);
      await streamRun(response.run_id);
      await loadWorkspace();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
      setStreaming(false);
    }
  }

  async function approve() {
    if (!runId || busy) return;
    if (selected.length === 0) {
      setError("Select at least one change to accept.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await apiFetch(`/api/runs/${runId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted_change_ids: selected }),
      });
      setNotice(`Approved ${selected.length} change(s). Created version v${result.version?.version_number ?? "?"}.`);
      setPreviewMode("base");
      setPreviewVersion(null);
      setRightTab("resume");
      setViewerOpen(false);
      const [payload] = await Promise.all([
        apiFetch(`/api/runs/${runId}`),
        loadMessages(conversationId),
        loadResume(resumeId),
        loadWorkspace(),
      ]);
      setRun(payload);
      setProposal(payload.proposal || null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    if (!runId || busy) return;
    setBusy(true);
    setError("");
    try {
      await apiFetch(`/api/runs/${runId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      setNotice("Proposal rejected. Your active version was left unchanged.");
      setProposal(null);
      setPreviewMode("base");
      setRightTab("resume");
      const [payload] = await Promise.all([
        apiFetch(`/api/runs/${runId}`),
        loadMessages(conversationId),
        loadResume(resumeId),
      ]);
      setRun(payload);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function historyAction(action: "undo" | "redo") {
    if (!resumeId || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await apiFetch(`/api/resumes/${resumeId}/${action}`, { method: "POST" });
      setNotice(result.message || `${action === "undo" ? "Undo" : "Redo"} applied.`);
      setPreviewVersion(null);
      setPreviewMode("base");
      setRightTab("resume");
      await loadResume(resumeId);
      await loadWorkspace();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function restoreVersion(version: Version) {
    if (!resumeId || busy) return;
    setBusy(true);
    setError("");
    try {
      await apiFetch(`/api/resumes/${resumeId}/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version_id: version.id }),
      });
      setNotice(`Restored v${version.version_number} as a new immutable version.`);
      setPreviewVersion(null);
      setPreviewMode("base");
      await loadResume(resumeId);
      await loadWorkspace();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function openVersion(version: Version) {
    if (!resumeId) return;
    if (version.is_active) {
      setPreviewVersion(null);
      setRightTab("resume");
      return;
    }
    try {
      const details = await apiFetch(`/api/resumes/${resumeId}/versions/${version.id}`);
      setPreviewVersion(details);
      setRightTab("resume");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function runCompare() {
    if (!resumeId || !compareA || !compareB) return;
    try {
      setCompare(
        await apiFetch(`/api/resumes/${resumeId}/compare`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version_a: compareA, version_b: compareB }),
        })
      );
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function newConversation(rid?: string | null) {
    const target = rid || resumes[0]?.id;
    if (!target) {
      setNotice("Upload a resume first, then start a chat.");
      return;
    }
    try {
      const created = await apiFetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_id: target }),
      });
      router.push(`/conversations/${created.id}`);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleUpload(file: File) {
    setBusy(true);
    setError("");
    setNotice("Parsing your resume…");
    try {
      const uploaded = await uploadResume(file);
      setNotice("Resume parsed and versioned.");
      await loadWorkspace();
      if (uploaded?.resume?.id) {
        await newConversation(uploaded.resume.id);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && (err.detail as any)?.existing_resume_id) {
        const existingResumeId = String((err.detail as any).existing_resume_id);
        setNotice("This resume is already in your workspace. Opening it…");
        await loadWorkspace();
        await newConversation(existingResumeId);
        return;
      }
      setError(errorMessage(err));
      setNotice("");
    } finally {
      setBusy(false);
    }
  }

  /* ------------------------------ derived ------------------------------ */
  const activeContent = resume?.active_version?.content;
  const pendingChanges = proposal?.changes?.filter((c) => c.status === "proposed") || [];

  const highlights = useMemo<HighlightMap>(() => {
    const map: HighlightMap = {};
    for (const change of proposal?.changes || []) {
      if (change.status !== "proposed") continue;
      if (previewMode === "proposal" && change.after) map[change.after] = "after";
      if (previewMode === "base" && change.before) map[change.before] = "before";
    }
    return map;
  }, [proposal, previewMode]);

  const shownContent =
    previewVersion?.content ||
    (previewMode === "proposal" && proposal?.candidate_content) ||
    activeContent;

  const shownHighlights = previewVersion ? undefined : highlights;
  const activeVersionNumber = resume?.active_version?.version_number;
  const lastEvent = events[events.length - 1];

  /* ------------------------------ render ------------------------------ */
  return (
    <div className="app-frame">
      <Sidebar
        resumes={resumes}
        conversations={conversations.filter((c) => !resumeId || c.resume_id === resumeId)}
        activeConversationId={conversationId}
        activeResumeId={resumeId}
        open={sidebarOpen}
        busy={busy}
        onClose={() => setSidebarOpen(false)}
        onNewChat={(id) => {
          setSidebarOpen(false);
          newConversation(id);
        }}
        onOpenConversation={(id) => {
          setSidebarOpen(false);
          router.push(`/conversations/${id}`);
        }}
        onSelectResume={(id) => {
          setSidebarOpen(false);
          newConversation(id);
        }}
        onUpload={(file) => {
          setSidebarOpen(false);
          handleUpload(file);
        }}
      />

      {sidebarOpen && (
        <button className="scrim side" style={{ zIndex: 45 }} onClick={() => setSidebarOpen(false)} aria-label="Close menu" tabIndex={-1} />
      )}

      <section className="chat-column">
        <header className="chat-head">
          <button className="icon-btn mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Open menu">
            <IconMenu size={16} />
          </button>
          <span className="brand-mark" style={{ width: 30, height: 30, fontSize: 12 }}>
            <IconSparkles size={15} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {conversation?.title || resume?.title || "New conversation"}
            </h1>
            <p>
              {run?.intent ? `${INTENT_LABELS[run.intent] || run.intent} · ` : ""}
              {proposal ? `${pendingChanges.length} proposed change(s)` : resume?.active_version ? `Active v${activeVersionNumber}` : "Upload a resume to begin"}
            </p>
          </div>
          <StatusPill status={run?.status} streaming={streaming} />
          <button className="icon-btn mobile-only" onClick={() => setViewerOpen(true)} aria-label="Open resume viewer">
            <IconFile size={16} />
          </button>
        </header>

        <div className="messages" ref={scrollRef}>
          <div className="messages-inner">
            {messages.length === 0 && !streaming && (
              <div className="empty-chat">
                <span className="brand-mark" style={{ width: 44, height: 44, margin: "0 auto", borderRadius: 13 }}>
                  <IconSparkles size={20} />
                </span>
                <h2>Let&apos;s make your resume sharper.</h2>
                <p>
                  Ask me to tailor your resume to a job, research a company, translate it for a new market,
                  or rewrite a section. I&apos;ll show every change before anything is saved.
                </p>
                <div className="prompt-grid">
                  {[
                    ["Match a job description", "Paste a JD and I’ll score the fit and propose evidence-backed edits."],
                    ["Optimize for a company", "Tell me the company and role; I’ll research and tailor."],
                    ["Strengthen experience", "Turn responsibilities into quantified, ATS-friendly bullets."],
                    ["Localize my resume", "Translate and adapt tone and conventions for another market."],
                  ].map(([title, description]) => (
                    <button
                      key={title}
                      className="prompt-card"
                      onClick={() => {
                        setInput(title);
                        if (title.includes("job description")) setShowJd(true);
                      }}
                    >
                      <strong>{title}</strong>
                      {description}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message) => (
              <div key={message.id} className={`msg ${message.role === "user" ? "user" : "assistant"}`}>
                {message.role !== "user" && (
                  <span className="msg-avatar">
                    <IconSparkles size={13} />
                  </span>
                )}
                <div className="msg-body">
                  <div className="bubble">
                    {message.role === "user" ? message.content : <SimpleMarkdown text={message.content} />}
                  </div>
                  <div className="msg-meta">
                    {message.role === "user" ? "You" : "Resume Studio"}
                    {message.created_at ? ` · ${timeOf(message.created_at)}` : ""}
                    {message.meta?.action === "proposal.approved" ? " · version created" : ""}
                  </div>
                </div>
              </div>
            ))}

            {streaming && (
              <div className="msg assistant">
                <span className="msg-avatar">
                  <IconSparkles size={13} />
                </span>
                <div className="msg-body">
                  <div className="typing-row">
                    <span className="typing-dots">
                      <span className="typing-dot" />
                      <span className="typing-dot" style={{ animationDelay: "0.15s" }} />
                      <span className="typing-dot" style={{ animationDelay: "0.3s" }} />
                    </span>
                    {lastEvent ? describeEvent(lastEvent) : "Thinking about your request…"}
                  </div>
                </div>
              </div>
            )}
          </div>

          {events.length > 0 && (
            <details className="activity" open={streaming}>
              <summary>Agent activity · {events.length} step(s)</summary>
              <div className="activity-log">
                {events.map((event, index) => (
                  <div className="activity-line" key={`${event.type}-${index}`}>
                    <time>{timeOf(event.created_at)}</time>
                    <span>{describeEvent(event)}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>

        <div className="composer-wrap">
          {error && (
            <div className="banner" style={{ marginBottom: 8 }}>
              <IconShield size={14} />
              <span style={{ flex: 1 }}>{error}</span>
              <button onClick={() => setError("")} aria-label="Dismiss" style={{ background: "none", border: "none", color: "inherit" }}>
                <IconX size={13} />
              </button>
            </div>
          )}
          {notice && (
            <div className="notice" style={{ maxWidth: 780, margin: "0 auto 8px" }}>
              {notice}
            </div>
          )}

          <div className="composer">
            <label className="sr-only" htmlFor="chat-input">Message</label>
            <textarea
              id="chat-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder="Ask for analysis, tailoring, translation, or edits…"
              rows={2}
              disabled={busy && !streaming}
            />

            {showJd && (
              <div className="composer-jd">
                <label className="sr-only" htmlFor="jd-input">Job description</label>
                <textarea
                  id="jd-input"
                  value={jd}
                  onChange={(event) => setJd(event.target.value)}
                  placeholder="Paste the job description here. It stays attached to this conversation."
                  rows={4}
                />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 3 }}>
                  <span style={{ fontSize: 10, color: "var(--muted)" }}>{jd.length.toLocaleString()} characters</span>
                  <button className="tool-btn" onClick={() => setShowJd(false)}>Hide</button>
                </div>
              </div>
            )}

            <div className="composer-bar">
              <button className={`tool-btn ${showJd ? "on" : ""}`} onClick={() => setShowJd((value) => !value)}>
                <IconBriefcase size={13} />
                Job description
              </button>
              {jdAttached && !showJd && <span className="chip indigo">JD attached</span>}
              <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)", marginRight: 6 }}>
                {streaming ? "Agents are working…" : "Enter to send · Shift+Enter for a new line"}
              </span>
              <button className="send-btn" onClick={send} disabled={!input.trim() || streaming || busy}>
                <IconArrowUp size={14} />
                Send
              </button>
            </div>
          </div>
          <p className="hint">
            AI output is a proposal, not a fact. Verify anything you accept; unsupported claims are blocked automatically.
          </p>
        </div>
      </section>

      {viewerOpen && (
        <button className="scrim viewer" onClick={() => setViewerOpen(false)} aria-label="Close resume viewer" tabIndex={-1} />
      )}

      <aside className={`viewer ${viewerOpen ? "open" : ""}`} aria-label="Resume viewer">
        <div className="viewer-head">
          <div className="viewer-title-row">
            <span className="viewer-title">
              <strong>{resume?.title || "Resume"}</strong>
              <span>
                {previewVersion
                  ? `Previewing v${previewVersion.version_number}`
                  : previewMode === "proposal"
                    ? "Proposed version preview"
                    : resume?.active_version
                      ? `Active version v${activeVersionNumber}`
                      : "No active version"}
              </span>
            </span>
            <button className="icon-btn mobile-only" onClick={() => setViewerOpen(false)} aria-label="Close viewer">
              <IconX size={15} />
            </button>
          </div>
          <div className="tabs">
            <button className={`tab ${rightTab === "resume" ? "active" : ""}`} onClick={() => setRightTab("resume")}>
              Resume
            </button>
            <button className={`tab ${rightTab === "changes" ? "active" : ""}`} onClick={() => setRightTab("changes")}>
              Changes {pendingChanges.length > 0 && <span className="count">{pendingChanges.length}</span>}
            </button>
            <button className={`tab ${rightTab === "history" ? "active" : ""}`} onClick={() => setRightTab("history")}>
              History
            </button>
          </div>
        </div>

        <div className="viewer-body">
          {rightTab === "resume" && (
            <>
              <div className="viewer-toolbar">
                {proposal && !previewVersion && (
                  <div className="preview-toggle">
                    <button className={previewMode === "base" ? "active" : ""} onClick={() => setPreviewMode("base")}>
                      Current
                    </button>
                    <button className={previewMode === "proposal" ? "active" : ""} onClick={() => setPreviewMode("proposal")}>
                      Proposed
                    </button>
                  </div>
                )}
                {previewVersion && (
                  <button
                    className="small-btn"
                    onClick={() => {
                      setPreviewVersion(null);
                      setPreviewMode("base");
                    }}
                  >
                    <IconRefresh size={12} /> Back to active
                  </button>
                )}
                <span className="chip slate">
                  {previewVersion
                    ? `v${previewVersion.version_number}`
                    : previewMode === "proposal"
                      ? "Preview"
                      : `v${activeVersionNumber ?? "—"}`}
                </span>
                <div style={{ marginLeft: "auto", display: "flex", gap: 5 }}>
                  <button
                    className="small-btn"
                    disabled={!resumeId}
                    onClick={() => resumeId && downloadExport(resumeId, "docx", previewVersion?.id).catch((err) => setError(errorMessage(err)))}
                  >
                    <IconDownload size={12} /> DOCX
                  </button>
                  <button
                    className="small-btn"
                    disabled={!resumeId}
                    onClick={() => resumeId && downloadExport(resumeId, "pdf", previewVersion?.id).catch((err) => setError(errorMessage(err)))}
                  >
                    <IconDownload size={12} /> PDF
                  </button>
                </div>
              </div>

              {previewVersion && (
                <div className="notice">
                  <strong>Read-only preview</strong>
                  You are viewing v{previewVersion.version_number}. Restore it from History to make it active.
                </div>
              )}
              {previewMode === "proposal" && proposal && (
                <div className="notice warn">
                  <strong>Proposed candidate — not saved</strong>
                  Highlighted text will change if you approve. Nothing is written until you approve.
                </div>
              )}

              <ResumeDocument
                content={shownContent}
                highlights={shownHighlights}
                emptyLabel={resumeId ? "This resume has no parsed content." : "Upload a PDF or DOCX to see your resume here."}
              />
            </>
          )}

          {rightTab === "changes" && (
            <>
              {!proposal && (
                <div className="notice">
                  <strong>No changes waiting</strong>
                  Ask the assistant to tailor or improve your resume. Proposed edits will appear here for per-change approval.
                </div>
              )}

              {proposal && (
                <>
                  <div className="notice">
                    <strong>Review before saving</strong>
                    {proposal.summary || `${proposal.changes.length} proposed change(s).`} Unchecked changes will not be saved.
                  </div>

                  {proposal.validation?.unsupported_claims && proposal.validation.unsupported_claims.length > 0 && (
                    <div className="notice warn">
                      <strong>Blocked unsupported claims</strong>
                      {proposal.validation.unsupported_claims.slice(0, 4).join(" · ")}
                    </div>
                  )}
                  {proposal.validation?.warnings && proposal.validation.warnings.length > 0 && (
                    <div className="notice warn">
                      <strong>Validation notes</strong>
                      {proposal.validation.warnings.slice(0, 4).join(" · ")}
                    </div>
                  )}

                  <div className="viewer-toolbar">
                    <button
                      className="small-btn"
                      onClick={() => setSelected(pendingChanges.map((change) => change.id))}
                      disabled={pendingChanges.length === 0 || proposal.status !== "pending"}
                    >
                      <IconCheck size={12} /> Select all
                    </button>
                    <button
                      className="small-btn"
                      onClick={() => setSelected([])}
                      disabled={pendingChanges.length === 0 || proposal.status !== "pending"}
                    >
                      Clear
                    </button>
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>
                      {selected.length}/{pendingChanges.length} selected
                    </span>
                  </div>

                  {proposal.changes.map((change) => {
                    const isProposed = change.status === "proposed";
                    return (
                      <div className={`change-card ${isProposed ? "" : "blocked"}`} key={change.id}>
                        <div className="change-top">
                          <input
                            type="checkbox"
                            aria-label={`Accept change to ${change.field_path}`}
                            checked={selected.includes(change.id)}
                            disabled={!isProposed || proposal.status !== "pending" || busy}
                            onChange={(event) => {
                              setSelected((current) =>
                                event.target.checked
                                  ? [...current, change.id]
                                  : current.filter((id) => id !== change.id)
                              );
                            }}
                          />
                          <span className="change-path">{prettyPath(change.field_path)}</span>
                          <span className={`chip ${isProposed ? "indigo" : "rose"}`}>
                            {isProposed ? AGENT_LABELS[change.agent || ""] || "Proposal" : change.status}
                          </span>
                        </div>
                        <div className="change-diff">
                          {change.operation === "add" ? (
                            <div className="after">+ {change.after}</div>
                          ) : change.operation === "remove" ? (
                            <div className="before">− {change.before}</div>
                          ) : (
                            <>
                              {change.before && <div className="before">{change.before}</div>}
                              {change.after && <div className="after">{change.after}</div>}
                            </>
                          )}
                        </div>
                        {change.requires_user_confirmation && (
                          <div className="change-reason warn">Confirm you genuinely have this before accepting.</div>
                        )}
                        {change.reason && <div className="change-reason">Why: {change.reason}</div>}
                      </div>
                    );
                  })}

                  {proposal.status === "pending" ? (
                    <div className="change-actions">
                      <button className="btn primary" onClick={approve} disabled={busy || selected.length === 0}>
                        <IconCheck size={13} />
                        {busy ? "Saving…" : `Approve ${selected.length}`}
                      </button>
                      <button className="btn ghost" onClick={reject} disabled={busy}>
                        <IconX size={13} /> Reject all
                      </button>
                    </div>
                  ) : (
                    <div className={`notice ${proposal.status === "rejected" ? "warn" : ""}`} style={{ marginTop: 10 }}>
                      <strong>Proposal {proposal.status.replace(/_/g, " ")}</strong>
                      No further action is needed. The version history is preserved.
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {rightTab === "history" && (
            <>
              <div className="viewer-toolbar">
                <button className="small-btn" onClick={() => historyAction("undo")} disabled={!canUndo || busy}>
                  <IconUndo size={12} /> Undo
                </button>
                <button className="small-btn" onClick={() => historyAction("redo")} disabled={!canRedo || busy}>
                  <IconRedo size={12} /> Redo
                </button>
                <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>
                  {versions.length} immutable version(s)
                </span>
              </div>

              <div className="notice">
                Every accepted proposal creates a new version. Undo and redo also append versions, so history is never rewritten.
              </div>

              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--muted)", marginBottom: 5 }}>COMPARE VERSIONS</div>
                <div className="compare-row">
                  <select className="select" value={compareA} onChange={(event) => setCompareA(event.target.value)}>
                    <option value="">Version A</option>
                    {versions.map((version) => (
                      <option key={version.id} value={version.id}>v{version.version_number} · {version.change_summary}</option>
                    ))}
                  </select>
                  <select className="select" value={compareB} onChange={(event) => setCompareB(event.target.value)}>
                    <option value="">Version B</option>
                    {versions.map((version) => (
                      <option key={version.id} value={version.id}>v{version.version_number} · {version.change_summary}</option>
                    ))}
                  </select>
                </div>
                <button className="small-btn" style={{ marginTop: 6 }} onClick={runCompare} disabled={!compareA || !compareB}>
                  Compare
                </button>
                {compare && (
                  <div style={{ marginTop: 8 }}>
                    {(compare.diff || []).length === 0 && <p style={{ fontSize: 10.5, color: "var(--muted)" }}>No differences.</p>}
                    {(compare.diff || []).slice(0, 40).map((item: any, index: number) => (
                      <div className="diff-line" key={index}>
                        <strong>{prettyPath(item.path)}</strong> · {item.kind}
                        {item.before !== undefined && <div className="d-before">− {String(item.before).slice(0, 220)}</div>}
                        {item.after !== undefined && <div className="d-after">+ {String(item.after).slice(0, 220)}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {versions.map((version) => (
                <div className={`version-row ${version.is_active ? "active" : ""}`} key={version.id}>
                  <span className="version-num">v{version.version_number}</span>
                  <div className="version-body">
                    <strong>
                      {version.change_summary || "Resume revision"}
                      {version.is_active && <span className="chip green" style={{ marginLeft: 6 }}>active</span>}
                    </strong>
                    <p>{version.created_at ? new Date(version.created_at).toLocaleString() : ""}</p>
                    <div className="version-actions">
                      <button className="small-btn" onClick={() => openVersion(version)}>
                        Preview
                      </button>
                      {!version.is_active && (
                        <button className="small-btn" onClick={() => restoreVersion(version)} disabled={busy}>
                          Restore
                        </button>
                      )}
                      <button
                        className="small-btn"
                        onClick={() => resumeId && downloadExport(resumeId, "docx", version.id).catch((err) => setError(errorMessage(err)))}
                      >
                        DOCX
                      </button>
                      <button
                        className="small-btn"
                        onClick={() => resumeId && downloadExport(resumeId, "pdf", version.id).catch((err) => setError(errorMessage(err)))}
                      >
                        PDF
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

export default function ConversationPage() {
  return (
    <Suspense fallback={<div style={{ padding: 24, color: "var(--muted)" }}>Loading workspace…</div>}>
      <Workspace />
    </Suspense>
  );
}
