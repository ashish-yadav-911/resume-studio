"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, apiUrl, authHeaders, uploadResume } from "@/lib/api";

type Msg = { id: string; role: string; content: string; run_id?: string | null; meta?: any };

function ResumeView({ content }: { content: any }) {
  if (!content) return <p className="text-sm text-slate-500">No resume content available.</p>;
  const p = content.personal_information || {};
  return (
    <div className="space-y-4 text-sm">
      <div className="border-b border-indigo-100 pb-3">
        <h3 className="text-xl font-bold text-indigo-700">{p.full_name || "Resume"}</h3>
        <p className="text-sm text-slate-600">{p.headline}</p>
        <p className="text-xs text-slate-500">{[p.email, p.phone, p.location, ...(p.links || [])].filter(Boolean).join(" · ")}</p>
      </div>
      {content.professional_summary && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Professional Summary</h4>
          <p>{content.professional_summary}</p>
        </section>
      )}
      {(content.experience || []).length > 0 && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Experience</h4>
          <div className="space-y-3">
            {content.experience.map((e: any, i: number) => (
              <div key={i}>
                <div className="font-semibold">{e.role} — {e.company}</div>
                <div className="text-xs text-slate-500">{[e.location, `${e.start_date} - ${e.current ? "Present" : e.end_date}`].filter(Boolean).join(" · ")}</div>
                <ul className="list-disc ml-5 mt-1">
                  {(e.bullets || []).map((b: string, j: number) => <li key={j}>{b}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </section>
      )}
      {(content.skills || []).length > 0 && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Skills</h4>
          {content.skills.map((g: any, i: number) => (
            <div key={i}><span className="font-semibold">{g.category}:</span> {(g.items || []).join(", ")}</div>
          ))}
        </section>
      )}
      {(content.projects || []).length > 0 && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Projects</h4>
          {content.projects.map((pr: any, i: number) => (
            <div key={i} className="mb-2">
              <div className="font-semibold">{pr.name}</div>
              <p>{pr.description}</p>
              {(pr.bullets || []).length > 0 && <ul className="list-disc ml-5">{pr.bullets.map((b: string, j: number) => <li key={j}>{b}</li>)}</ul>}
            </div>
          ))}
        </section>
      )}
      {(content.education || []).length > 0 && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Education</h4>
          {content.education.map((ed: any, i: number) => (
            <div key={i}>{ed.degree} {ed.field_of_study} — {ed.institution} ({ed.start_date} - {ed.end_date})</div>
          ))}
        </section>
      )}
      {(content.certifications || []).length > 0 && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Certifications</h4>
          {content.certifications.map((c: any, i: number) => <div key={i}>{c.name} — {c.issuer} {c.date}</div>)}
        </section>
      )}
      {(content.languages || []).length > 0 && (
        <section>
          <h4 className="font-bold text-xs uppercase tracking-wide text-indigo-600 mb-1">Languages</h4>
          {content.languages.map((l: any, i: number) => <div key={i}>{l.language} — {l.proficiency}</div>)}
        </section>
      )}
    </div>
  );
}

function ConversationInner() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [jd, setJd] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [typing, setTyping] = useState(false);
  const [finalResponse, setFinalResponse] = useState("");
  const [proposal, setProposal] = useState<any>(null);
  const [selectedChanges, setSelectedChanges] = useState<string[]>([]);
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<any>(null);
  const [resume, setResume] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [reasoning, setReasoning] = useState<any[]>([]);
  const [resumes, setResumes] = useState<any[]>([]);
  const [conversations, setConversations] = useState<any[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [compareResult, setCompareResult] = useState<any>(null);
  const streamRef = useRef<AbortController | null>(null);

  async function loadAll() {
    try {
      const rs = await apiFetch("/api/resumes");
      setResumes(rs);
      const cs = await apiFetch("/api/conversations");
      setConversations(cs);
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function loadConversation() {
    if (!id) return;
    try {
      const c = await apiFetch(`/api/conversations/${id}`);
      setConversation(c);
      setResumeId(c.resume_id);
      const msgs = await apiFetch(`/api/conversations/${id}/messages`);
      setMessages(msgs);
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function loadResume(rid: string) {
    try {
      const r = await apiFetch(`/api/resumes/${rid}`);
      setResume(r);
      const v = await apiFetch(`/api/resumes/${rid}/versions`);
      setVersions(v.versions || []);
    } catch (e) {
      setStatus(String(e));
    }
  }

  useEffect(() => {
    loadAll();
    loadConversation();
  }, [id]);

  useEffect(() => {
    if (resumeId) loadResume(resumeId);
  }, [resumeId]);

  async function streamEvents(rid: string) {
    if (streamRef.current) streamRef.current.abort();
    const controller = new AbortController();
    streamRef.current = controller;
    setTyping(true);
    setReasoning((prev) => [...prev, { type: "run.started", message: "Agent run started", time: new Date().toLocaleTimeString() }]);
    try {
      const res = await fetch(apiUrl(`/api/runs/${rid}/events`), { headers: authHeaders(), signal: controller.signal });
      if (!res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let data = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (!data) continue;
          try {
            const evt = JSON.parse(data);
            const pretty = evt.type + (evt.payload?.message ? `: ${evt.payload.message}` : evt.payload?.summary ? `: ${evt.payload.summary}` : "");
            setReasoning((prev) => [...prev, { type: evt.type, message: pretty, time: new Date().toLocaleTimeString(), payload: evt.payload }]);
            if (["run.completed", "run.awaiting_approval", "run.clarification", "run.failed"].includes(evt.type)) {
              setTyping(false);
              setFinalResponse(evt.payload?.final_response || "");
              setStatus(evt.type);
              const run = await apiFetch(`/api/runs/${rid}`);
              setProposal(run.proposal || null);
              if (run.proposal?.changes) {
                setSelectedChanges(run.proposal.changes.filter((c: any) => c.status === "proposed").map((c: any) => c.id));
              }
              const msgs = await apiFetch(`/api/conversations/${id}/messages`);
              setMessages(msgs);
              if (resumeId) await loadResume(resumeId);
            }
          } catch {
            // ignore
          }
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) setStatus(String(e));
    } finally {
      setTyping(false);
    }
  }

  async function send() {
    if (!input.trim() || typing) return;
    const optimistic: Msg = { id: `local-${Date.now()}`, role: "user", content: input };
    setMessages((prev) => [...prev, optimistic]);
    setStatus("Sending...");
    setFinalResponse("");
    setProposal(null);
    const currentInput = input;
    const currentJd = jd;
    setInput("");
    try {
      const res = await apiFetch(`/api/conversations/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: currentInput, job_description: currentJd.trim() || undefined }),
        headers: { "Content-Type": "application/json" },
      });
      setMessages((prev) => prev.map((m) => (m.id === optimistic.id ? res.message : m)));
      setRunId(res.run_id);
      await streamEvents(res.run_id);
    } catch (e) {
      setStatus(String(e));
      setTyping(false);
    }
  }

  async function approve() {
    if (!runId) return;
    setStatus("Approving...");
    try {
      await apiFetch(`/api/runs/${runId}/approve`, {
        method: "POST",
        body: JSON.stringify({ accepted_change_ids: selectedChanges.length ? selectedChanges : null }),
        headers: { "Content-Type": "application/json" },
      });
      setStatus("Approved");
      if (resumeId) await loadResume(resumeId);
      const msgs = await apiFetch(`/api/conversations/${id}/messages`);
      setMessages(msgs);
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function reject() {
    if (!runId) return;
    setStatus("Rejecting...");
    try {
      await apiFetch(`/api/runs/${runId}/reject`, { method: "POST", body: JSON.stringify({}), headers: { "Content-Type": "application/json" } });
      setStatus("Rejected");
      const msgs = await apiFetch(`/api/conversations/${id}/messages`);
      setMessages(msgs);
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function undo() {
    if (!resumeId) return;
    try {
      await apiFetch(`/api/resumes/${resumeId}/undo`, { method: "POST" });
      await loadResume(resumeId);
      setStatus("Undo applied");
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function redo() {
    if (!resumeId) return;
    try {
      await apiFetch(`/api/resumes/${resumeId}/redo`, { method: "POST" });
      await loadResume(resumeId);
      setStatus("Redo applied");
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function compareVersions() {
    if (!resumeId || !compareA || !compareB) return;
    try {
      const r = await apiFetch(`/api/resumes/${resumeId}/compare`, {
        method: "POST",
        body: JSON.stringify({ version_a: compareA, version_b: compareB }),
        headers: { "Content-Type": "application/json" },
      });
      setCompareResult(r);
    } catch (e) {
      setStatus(String(e));
    }
  }

  async function newConversation(rid: string) {
    const conv = await apiFetch("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ resume_id: rid }),
      headers: { "Content-Type": "application/json" },
    });
    router.push(`/conversations/${conv.id}`);
  }

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    const fileInput = (e.target as any).elements.namedItem("file") as HTMLInputElement;
    const file = fileInput?.files?.[0];
    if (!file) return;
    setStatus("Uploading...");
    try {
      const uploaded = await uploadResume(file);
      await loadAll();
      if (uploaded?.resume?.id) await newConversation(uploaded.resume.id);
    } catch (err) {
      setStatus(String(err));
    }
  }

  const activeContent = resume?.active_version?.content;

  return (
    <div className="h-[calc(100vh-88px)] grid grid-cols-1 gap-4 xl:grid-cols-[260px_minmax(0,1fr)_430px]">
      <aside className="card p-4 overflow-y-auto">
        <h2 className="text-sm font-bold uppercase tracking-wide text-indigo-600 mb-3">Workspace</h2>
        <form onSubmit={onUpload} className="mb-4 space-y-2">
          <input name="file" type="file" accept=".pdf,.docx" className="input w-full text-xs" />
          <button type="submit" className="button w-full text-sm">Upload Resume</button>
        </form>

        <div className="mb-4">
          <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">Resumes</h3>
          <div className="space-y-1">
            {resumes.map((r: any) => (
              <button key={r.id} className="w-full text-left rounded-lg border border-indigo-100 bg-white px-3 py-2 text-sm hover:border-indigo-300 hover:bg-indigo-50" onClick={() => newConversation(r.id)}>
                {r.title}
              </button>
            ))}
            {resumes.length === 0 && <p className="text-xs text-slate-500">No resumes</p>}
          </div>
        </div>

        <div className="mb-4">
          <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">Conversations</h3>
          <div className="space-y-1">
            {conversations.map((c: any) => (
              <a key={c.id} href={`/conversations/${c.id}`} className={`block rounded-lg border px-3 py-2 text-sm ${c.id === id ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-indigo-100 bg-white hover:bg-indigo-50"}`}>
                {c.title}
              </a>
            ))}
          </div>
        </div>

        {resumeId && (
          <div className="mb-4">
            <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500 mb-1">Versions</h3>
            <div className="space-y-1 max-h-44 overflow-y-auto">
              {versions.map((v: any) => (
                <div key={v.id} className="rounded-lg border border-indigo-100 bg-white px-3 py-2 text-xs">
                  <div className="font-semibold">v{v.version_number} {v.is_active ? "(active)" : ""}</div>
                  <div className="text-slate-500">{v.change_summary}</div>
                  <div className="flex gap-1 mt-2">
                    <a className="button-secondary text-[10px]" href={apiUrl(`/api/resumes/${resumeId}/export?format=docx&version_id=${v.id}`)} target="_blank">DOCX</a>
                    <a className="button-secondary text-[10px]" href={apiUrl(`/api/resumes/${resumeId}/export?format=pdf&version_id=${v.id}`)} target="_blank">PDF</a>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex gap-2 mt-3">
              <button className="button-secondary flex-1" onClick={undo}>Undo</button>
              <button className="button-secondary flex-1" onClick={redo}>Redo</button>
            </div>
            <button className="button w-full mt-2 text-sm" onClick={() => setShowCompare((s) => !s)}>Compare Versions</button>

            {showCompare && (
              <div className="mt-3 space-y-2">
                <select className="input w-full text-xs" value={compareA} onChange={(e) => setCompareA(e.target.value)}>
                  <option value="">Version A</option>
                  {versions.map((v: any) => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
                </select>
                <select className="input w-full text-xs" value={compareB} onChange={(e) => setCompareB(e.target.value)}>
                  <option value="">Version B</option>
                  {versions.map((v: any) => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
                </select>
                <button className="button w-full text-sm" onClick={compareVersions}>Compare</button>
                {compareResult && (
                  <div className="text-xs space-y-1 max-h-48 overflow-y-auto">
                    {(compareResult.diff || []).map((d: any, i: number) => (
                      <div key={i} className="rounded-lg border border-indigo-100 bg-white p-2">
                        <div className="font-semibold">{d.path} ({d.kind})</div>
                        {d.before !== undefined && <div className="text-rose-600">- {String(d.before).slice(0, 180)}</div>}
                        {d.after !== undefined && <div className="text-emerald-600">+ {String(d.after).slice(0, 180)}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        {status && <p className="text-xs mt-2 break-words text-slate-600">{status}</p>}
      </aside>

      <main className="card p-5 overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold text-indigo-700">{resume?.title || "Resume"}</h2>
          <span className="chip bg-cyan-50 text-cyan-700">{resume?.active_version ? `v${resume.active_version.version_number}` : "No version"}</span>
        </div>

        <ResumeView content={activeContent} />

        {proposal && (
          <div className="mt-6 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-4">
            <h3 className="text-lg font-bold text-indigo-700 mb-2">Proposed Changes</h3>
            <p className="text-sm text-slate-600 mb-3">{proposal.summary}</p>
            <div className="space-y-2">
              {(proposal.changes || []).map((c: any) => (
                <div key={c.id} className="rounded-xl border border-indigo-100 bg-white p-3 text-sm">
                  <div className="flex items-center gap-2 mb-1">
                    <input
                      type="checkbox"
                      checked={selectedChanges.includes(c.id)}
                      disabled={c.status !== "proposed"}
                      onChange={(e) => {
                        if (e.target.checked) setSelectedChanges((s) => [...s, c.id]);
                        else setSelectedChanges((s) => s.filter((x) => x !== c.id));
                      }}
                    />
                    <span className="font-semibold">{c.field_path}</span>
                    <span className="chip bg-slate-100 text-slate-600">{c.status}</span>
                  </div>
                  <div><span className="font-semibold text-rose-600">Before:</span> {c.before}</div>
                  <div><span className="font-semibold text-emerald-600">After:</span> {c.after}</div>
                  <div className="text-slate-600"><span className="font-semibold">Reason:</span> {c.reason}</div>
                </div>
              ))}
            </div>
            <div className="flex gap-2 mt-3">
              <button className="button" onClick={approve}>Approve Selected</button>
              <button className="button-secondary" onClick={reject}>Reject</button>
            </div>
          </div>
        )}
      </main>

      <aside className="grid grid-rows-[1fr_auto] gap-4">
        <section className="card p-4 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold uppercase tracking-wide text-indigo-600">Conversation</h2>
            {typing && <span className="chip bg-cyan-50 text-cyan-700">Agent typing</span>}
          </div>
          <div className="flex-1 overflow-y-auto space-y-3 pr-1">
            {messages.map((m) => (
              <div key={m.id} className={m.role === "user" ? "bubble-user" : "bubble-assistant"}>
                <div className="text-[10px] uppercase tracking-wide opacity-70 mb-1">{m.role}</div>
                <div className="whitespace-pre-wrap">{m.content}</div>
              </div>
            ))}
            {typing && (
              <div className="bubble-assistant flex items-center gap-1">
                <span className="typing-dot" />
                <span className="typing-dot" style={{ animationDelay: "0.15s" }} />
                <span className="typing-dot" style={{ animationDelay: "0.3s" }} />
              </div>
            )}
            {messages.length === 0 && !typing && <p className="text-sm text-slate-500">Start chatting about your resume.</p>}
          </div>

          <div className="mt-3 space-y-2">
            <textarea className="input w-full text-sm" rows={3} placeholder="Ask for analysis, tailoring, translation, or edits..." value={input} onChange={(e) => setInput(e.target.value)} />
            <textarea className="input w-full text-sm" rows={2} placeholder="Optional job description..." value={jd} onChange={(e) => setJd(e.target.value)} />
            <button className="button w-full" onClick={send} disabled={typing}>Send</button>
          </div>

          {finalResponse && (
            <details className="mt-3 rounded-xl border border-indigo-100 bg-white p-3">
              <summary>Latest agent response</summary>
              <pre className="whitespace-pre-wrap text-xs text-slate-700">{finalResponse}</pre>
            </details>
          )}
        </section>

        <details className="card p-4 max-h-56 overflow-y-auto">
          <summary>Agent reasoning ({reasoning.length})</summary>
          <div className="mt-2 space-y-1 text-xs">
            {reasoning.map((r, i) => (
              <div key={i} className="rounded-lg bg-slate-50 px-2 py-1">
                <span className="font-semibold text-indigo-600">{r.time}</span> {r.message}
              </div>
            ))}
            {reasoning.length === 0 && <p className="text-slate-500">No agent activity yet.</p>}
          </div>
        </details>
      </aside>
    </div>
  );
}

export default function ConversationPage() {
  return (
    <Suspense fallback={<div className="text-sm text-slate-500">Loading workspace...</div>}>
      <ConversationInner />
    </Suspense>
  );
}