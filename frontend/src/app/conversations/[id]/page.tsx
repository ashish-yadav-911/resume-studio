"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { apiFetch, apiUrl, authHeaders, uploadResume } from "@/lib/api";

type Msg = { id: string; role: string; content: string; run_id?: string | null; meta?: any };

function ResumeView({ content }: { content: any }) {
  if (!content) return <p className="text-sm">No resume content available.</p>;
  const p = content.personal_information || {};
  return (
    <div className="space-y-4 text-sm">
      <div className="border-b border-black pb-3">
        <h3 className="text-xl font-bold">{p.full_name || "Resume"}</h3>
        <p className="text-sm">{p.headline}</p>
        <p className="text-xs">{[p.email, p.phone, p.location, ...(p.links || [])].filter(Boolean).join(" · ")}</p>
      </div>
      {content.professional_summary && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Professional Summary</h4>
          <p>{content.professional_summary}</p>
        </section>
      )}
      {(content.experience || []).length > 0 && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Experience</h4>
          <div className="space-y-3">
            {content.experience.map((e: any, i: number) => (
              <div key={i}>
                <div className="font-bold">{e.role} — {e.company}</div>
                <div className="text-xs">{[e.location, `${e.start_date} - ${e.current ? "Present" : e.end_date}`].filter(Boolean).join(" · ")}</div>
                <ul className="list-disc ml-5">
                  {(e.bullets || []).map((b: string, j: number) => <li key={j}>{b}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </section>
      )}
      {(content.skills || []).length > 0 && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Skills</h4>
          {content.skills.map((g: any, i: number) => (
            <div key={i}><span className="font-bold">{g.category}:</span> {(g.items || []).join(", ")}</div>
          ))}
        </section>
      )}
      {(content.projects || []).length > 0 && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Projects</h4>
          {content.projects.map((pr: any, i: number) => (
            <div key={i} className="mb-2">
              <div className="font-bold">{pr.name}</div>
              <p>{pr.description}</p>
              {(pr.bullets || []).length > 0 && <ul className="list-disc ml-5">{pr.bullets.map((b: string, j: number) => <li key={j}>{b}</li>)}</ul>}
            </div>
          ))}
        </section>
      )}
      {(content.education || []).length > 0 && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Education</h4>
          {content.education.map((ed: any, i: number) => (
            <div key={i}>{ed.degree} {ed.field_of_study} — {ed.institution} ({ed.start_date} - {ed.end_date})</div>
          ))}
        </section>
      )}
      {(content.certifications || []).length > 0 && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Certifications</h4>
          {content.certifications.map((c: any, i: number) => <div key={i}>{c.name} — {c.issuer} {c.date}</div>)}
        </section>
      )}
      {(content.languages || []).length > 0 && (
        <section>
          <h4 className="font-bold uppercase text-xs mb-1">Languages</h4>
          {content.languages.map((l: any, i: number) => <div key={i}>{l.language} — {l.proficiency}</div>)}
        </section>
      )}
    </div>
  );
}

function ConversationInner() {
  const params = useParams();
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
    setReasoning((prev) => [...prev, { type: "run.started", message: "Run started", time: new Date().toLocaleTimeString() }]);
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
    if (!input.trim()) return;
    setStatus("Sending...");
    setFinalResponse("");
    setProposal(null);
    setReasoning([]);
    try {
      const res = await apiFetch(`/api/conversations/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: input, job_description: jd.trim() || undefined }),
        headers: { "Content-Type": "application/json" },
      });
      setMessages((prev) => [...prev, res.message]);
      setRunId(res.run_id);
      setInput("");
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
    window.location.href = `/conversations/${conv.id}`;
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
    <div className="h-[calc(100vh-72px)] grid grid-cols-[260px_1fr_420px] gap-3">
      {/* Left menu */}
      <aside className="bw-card p-3 overflow-y-auto">
        <h2 className="text-sm font-bold uppercase mb-3">Workspace</h2>
        <form onSubmit={onUpload} className="mb-4 space-y-2">
          <input name="file" type="file" accept=".pdf,.docx" className="bw-input w-full text-xs p-1" />
          <button type="submit" className="bw-button w-full px-2 py-1 text-xs">Upload Resume</button>
        </form>

        <div className="mb-4">
          <h3 className="text-xs font-bold uppercase mb-1">Resumes</h3>
          <div className="space-y-1">
            {resumes.map((r: any) => (
              <button key={r.id} className="w-full text-left border border-black px-2 py-1 text-xs hover:bg-black hover:text-white" onClick={() => newConversation(r.id)}>
                {r.title}
              </button>
            ))}
            {resumes.length === 0 && <p className="text-xs">No resumes</p>}
          </div>
        </div>

        <div className="mb-4">
          <h3 className="text-xs font-bold uppercase mb-1">Conversations</h3>
          <div className="space-y-1">
            {conversations.map((c: any) => (
              <a key={c.id} href={`/conversations/${c.id}`} className={`block border border-black px-2 py-1 text-xs ${c.id === id ? "bg-black text-white" : ""}`}>
                {c.title}
              </a>
            ))}
          </div>
        </div>

        {resumeId && (
          <div className="mb-4">
            <h3 className="text-xs font-bold uppercase mb-1">Versions</h3>
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {versions.map((v: any) => (
                <div key={v.id} className="border border-black px-2 py-1 text-xs">
                  <div>v{v.version_number} {v.is_active ? "(active)" : ""}</div>
                  <div className="text-[10px]">{v.change_summary}</div>
                  <div className="flex gap-1 mt-1">
                    <a className="bw-button px-1 py-0 text-[10px]" href={apiUrl(`/api/resumes/${resumeId}/export?format=docx&version_id=${v.id}`)} target="_blank">DOCX</a>
                    <a className="bw-button px-1 py-0 text-[10px]" href={apiUrl(`/api/resumes/${resumeId}/export?format=pdf&version_id=${v.id}`)} target="_blank">PDF</a>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex gap-1 mt-2">
              <button className="bw-button px-2 py-1 text-xs" onClick={undo}>Undo</button>
              <button className="bw-button px-2 py-1 text-xs" onClick={redo}>Redo</button>
            </div>
            <button className="bw-button w-full px-2 py-1 text-xs mt-2" onClick={() => setShowCompare((s) => !s)}>Compare Versions</button>
          </div>
        )}
        {status && <p className="text-xs mt-2 break-words">{status}</p>}
      </aside>

      {/* Middle resume */}
      <main className="bw-card p-4 overflow-y-auto">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-bold">{resume?.title || "Resume"}</h2>
          <span className="text-xs">{resume?.active_version ? `v${resume.active_version.version_number}` : ""}</span>
        </div>

        {showCompare && resumeId && (
          <div className="border border-black p-3 mb-4 space-y-2">
            <h3 className="text-sm font-bold">Compare Versions</h3>
            <div className="flex gap-2">
              <select className="bw-input p-1 text-xs" value={compareA} onChange={(e) => setCompareA(e.target.value)}>
                <option value="">Version A</option>
                {versions.map((v: any) => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
              </select>
              <select className="bw-input p-1 text-xs" value={compareB} onChange={(e) => setCompareB(e.target.value)}>
                <option value="">Version B</option>
                {versions.map((v: any) => <option key={v.id} value={v.id}>v{v.version_number}</option>)}
              </select>
              <button className="bw-button px-2 py-1 text-xs" onClick={compareVersions}>Compare</button>
            </div>
            {compareResult && (
              <div className="text-xs space-y-1 max-h-60 overflow-y-auto">
                {(compareResult.diff || []).map((d: any, i: number) => (
                  <div key={i} className="border border-black p-1">
                    <div className="font-bold">{d.path} ({d.kind})</div>
                    {d.before !== undefined && <div className="text-red-900">- {String(d.before).slice(0, 200)}</div>}
                    {d.after !== undefined && <div className="text-green-900">+ {String(d.after).slice(0, 200)}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <ResumeView content={activeContent} />

        {proposal && (
          <div className="mt-6 border-t border-black pt-4">
            <h3 className="text-lg font-bold mb-2">Proposed Changes</h3>
            <p className="text-sm mb-2">{proposal.summary}</p>
            <div className="space-y-2">
              {(proposal.changes || []).map((c: any) => (
                <div key={c.id} className="border border-black p-2 text-sm">
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selectedChanges.includes(c.id)}
                      disabled={c.status !== "proposed"}
                      onChange={(e) => {
                        if (e.target.checked) setSelectedChanges((s) => [...s, c.id]);
                        else setSelectedChanges((s) => s.filter((x) => x !== c.id));
                      }}
                    />
                    <span className="font-bold">{c.field_path}</span>
                    <span className="text-xs uppercase">({c.status})</span>
                  </div>
                  <div><span className="font-bold">Before:</span> {c.before}</div>
                  <div><span className="font-bold">After:</span> {c.after}</div>
                  <div><span className="font-bold">Reason:</span> {c.reason}</div>
                </div>
              ))}
            </div>
            <div className="flex gap-2 mt-3">
              <button className="bw-button px-4 py-2" onClick={approve}>Approve Selected</button>
              <button className="bw-button px-4 py-2" onClick={reject}>Reject</button>
            </div>
          </div>
        )}
      </main>

      {/* Right chat + reasoning */}
      <aside className="grid grid-rows-[1fr_auto] gap-3">
        <section className="bw-card p-3 flex flex-col overflow-hidden">
          <h2 className="text-sm font-bold uppercase mb-2">Chat</h2>
          <div className="flex-1 overflow-y-auto space-y-3 border border-black p-2">
            {messages.map((m) => (
              <div key={m.id} className="border-b border-black pb-2">
                <div className="text-xs font-bold uppercase">{m.role}</div>
                <div className="text-sm whitespace-pre-wrap">{m.content}</div>
              </div>
            ))}
            {typing && (
              <div className="text-sm italic animate-pulse">Assistant is thinking…</div>
            )}
            {messages.length === 0 && !typing && <p className="text-sm">No messages yet.</p>}
          </div>
          <div className="mt-2 space-y-2">
            <textarea className="bw-input w-full p-2 text-sm" rows={3} placeholder="Message..." value={input} onChange={(e) => setInput(e.target.value)} />
            <textarea className="bw-input w-full p-2 text-sm" rows={2} placeholder="Optional job description..." value={jd} onChange={(e) => setJd(e.target.value)} />
            <button className="bw-button w-full px-4 py-2" onClick={send} disabled={typing}>Send</button>
          </div>
          {finalResponse && (
            <div className="mt-2 border border-black p-2 text-xs max-h-32 overflow-y-auto">
              <div className="font-bold uppercase mb-1">Latest response</div>
              <pre className="whitespace-pre-wrap">{finalResponse}</pre>
            </div>
          )}
        </section>

        <section className="bw-card p-3 h-48 overflow-y-auto">
          <h2 className="text-sm font-bold uppercase mb-2">Agent Reasoning</h2>
          <div className="space-y-1 text-xs">
            {reasoning.map((r, i) => (
              <div key={i} className="border-b border-black pb-1">
                <span className="font-bold">{r.time}</span> {r.message}
              </div>
            ))}
            {reasoning.length === 0 && <p>No agent activity yet.</p>}
          </div>
        </section>
      </aside>
    </div>
  );
}

export default function ConversationPage() {
  return (
    <Suspense fallback={<div className="text-sm">Loading...</div>}>
      <ConversationInner />
    </Suspense>
  );
}