"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { apiGet, apiPost } from "@/lib/api";

type DraftResponse = {
  draft_id: string;
  contact_id: string;
  tone_band: string;
  draft_subject: string;
  draft_text: string;
  citations_json: Array<{
    paragraph: number;
    chunk_id: string;
    interaction_id: string;
    span_json: Record<string, unknown>;
    snippet?: string;
  }>;
  status: string;
  objective?: string | null;
  retrieval_trace?: {
    objective_query?: string | null;
    recent_interactions?: Array<{
      interaction_id?: string;
      timestamp?: string;
      subject?: string | null;
    }>;
    vector_chunks?: Array<{
      chunk_id?: string;
      interaction_id?: string;
      score?: number | null;
      snippet?: string;
    }>;
    graph_claim_snippets?: string[];
  } | null;
  context_summary?: {
    display_name?: string | null;
    primary_email?: string | null;
    recent_interactions?: number;
    relevant_chunks?: number;
    graph_claim_snippets?: number;
  } | null;
};

type DraftStyleGuideUpdateResponse = {
  draft_id: string;
  updated: boolean;
  samples_used: number;
  guide_path: string;
  status: string;
};

type DraftObjectiveSuggestionResponse = {
  contact_id: string;
  objective: string;
  source_summary: {
    recent_subject?: string | null;
    vector_context_snippet?: string | null;
    graph_context_snippet?: string | null;
  };
};

type ContactContextResponse = {
  contact_id: string;
  profile: {
    contact_id: string;
    display_name: string | null;
    primary_email: string | null;
    company: string | null;
  } | null;
  interaction_summary: {
    total_interactions: number;
    brief: string;
    recent_subjects: string[];
  } | null;
};

export default function DraftsPage() {
  const [prefilledContactId, setPrefilledContactId] = useState("");

  const [objective, setObjective] = useState("");
  const [allowSensitive, setAllowSensitive] = useState(false);
  const [result, setResult] = useState<DraftResponse | null>(null);
  const [draftSubject, setDraftSubject] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [context, setContext] = useState<ContactContextResponse | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [revising, setRevising] = useState(false);
  const [updatingStyle, setUpdatingStyle] = useState(false);

  const contactId = useMemo(() => prefilledContactId.trim(), [prefilledContactId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    setPrefilledContactId(params.get("contactId") ?? "");
  }, []);

  useEffect(() => {
    if (!contactId) {
      setContext(null);
      setContextError(null);
      return;
    }

    let cancelled = false;
    setContextError(null);
    apiGet<ContactContextResponse>(`/scores/contact/${encodeURIComponent(contactId)}`)
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setContext(payload);
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        setContext(null);
        setContextError(err instanceof Error ? err.message : "Unable to load contact context");
      });
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  useEffect(() => {
    if (!contactId) {
      setObjective("");
      setResult(null);
      setDraftSubject("");
      setDraftBody("");
      return;
    }

    let cancelled = false;
    setError(null);
    setMessage(null);
    setResult(null);
    setDraftSubject("");
    setDraftBody("");

    async function initializeComposer(): Promise<void> {
      try {
        const latest = await apiGet<DraftResponse>(`/drafts/latest?contact_id=${encodeURIComponent(contactId)}`);
        if (cancelled) {
          return;
        }
        setResult(latest);
        setDraftSubject(latest.draft_subject);
        setDraftBody(latest.draft_text);
        if (latest.objective) {
          setObjective(latest.objective);
        }
        setMessage("Loaded latest saved draft for this contact.");
        return;
      } catch {
        // No latest draft found for this contact.
      }

      try {
        const suggestion = await apiGet<DraftObjectiveSuggestionResponse>(
          `/drafts/objective_suggestion?contact_id=${encodeURIComponent(contactId)}&allow_sensitive=false`,
        );
        if (cancelled) {
          return;
        }
        setObjective(suggestion.objective);
      } catch {
        if (cancelled) {
          return;
        }
        setObjective("Reconnect on current priorities and confirm next steps");
      }
    }

    void initializeComposer();
    return () => {
      cancelled = true;
    };
  }, [contactId]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!contactId) {
      setError("Open Draft Composer from a contact profile so the contact is preselected.");
      return;
    }
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      const shouldOverwrite = result && result.contact_id === contactId;
      const response = await apiPost<DraftResponse>("/drafts", {
        contact_id: contactId,
        objective,
        allow_sensitive: allowSensitive,
        overwrite_draft_id: shouldOverwrite ? result.draft_id : undefined,
      });
      setResult(response);
      setDraftSubject(response.draft_subject);
      setDraftBody(response.draft_text);
      if (response.objective) {
        setObjective(response.objective);
      }
      setMessage(shouldOverwrite ? "Draft regenerated and existing draft overwritten." : "Draft generated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  async function onRevise(status: "edited" | "approved") {
    if (!result) {
      return;
    }
    setError(null);
    setMessage(null);
    setRevising(true);
    try {
      const response = await apiPost<DraftResponse>(`/drafts/${encodeURIComponent(result.draft_id)}/revise`, {
        draft_subject: draftSubject,
        draft_body: draftBody,
        status,
      });
      setResult(response);
      setDraftSubject(response.draft_subject);
      setDraftBody(response.draft_text);
      setMessage(status === "approved" ? "Draft saved and marked approved." : "Draft revision saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save revision");
    } finally {
      setRevising(false);
    }
  }

  async function onUpdateWritingStyleGuide() {
    if (!result) {
      return;
    }
    setError(null);
    setMessage(null);
    setUpdatingStyle(true);
    try {
      const response = await apiPost<DraftStyleGuideUpdateResponse>(
        `/drafts/${encodeURIComponent(result.draft_id)}/update_writing_style`,
        {},
      );
      if (response.updated) {
        setMessage(`Writing style guide updated using ${response.samples_used} revised samples.`);
      } else {
        setMessage("Writing style guide update did not run.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update writing style guide");
    } finally {
      setUpdatingStyle(false);
    }
  }

  return (
    <section>
      <h1 className="sectionTitle">Draft Composer</h1>
      {contactId ? (
        <p className="muted">
          Drafting for contact <code>{contactId}</code>. <Link href="/">Back to Priority Contacts</Link>
        </p>
      ) : (
        <p className="muted">
          Open this page from a contact profile to compose a draft for that contact. <Link href="/">Back to Priority Contacts</Link>
        </p>
      )}
      {context && (
        <article className="card">
          <p className="label">Contact Context</p>
          <div className="grid">
            <div>
              <div className="label">Name</div>
              <div className="value">{context.profile?.display_name ?? "Not available"}</div>
            </div>
            <div>
              <div className="label">Email</div>
              <div className="value">{context.profile?.primary_email ?? "Not available"}</div>
            </div>
            <div>
              <div className="label">Company</div>
              <div className="value">{context.profile?.company ?? "Not available"}</div>
            </div>
            <div>
              <div className="label">Interactions</div>
              <div className="value">{context.interaction_summary?.total_interactions ?? 0}</div>
            </div>
          </div>
          <p className="muted">{context.interaction_summary?.brief ?? "No interaction summary available yet."}</p>
          {context.interaction_summary?.recent_subjects?.length ? (
            <p className="muted">Recent topics: {context.interaction_summary.recent_subjects.join(" | ")}</p>
          ) : null}
        </article>
      )}
      <form className="card" onSubmit={onSubmit}>
        <label className="label" htmlFor="objective">
          Objective
        </label>
        <input id="objective" value={objective} onChange={(e) => setObjective(e.target.value)} />

        <div className="formActionsRow composerGenerateRow">
          <label className="checkboxInline">
            <input type="checkbox" checked={allowSensitive} onChange={(e) => setAllowSensitive(e.target.checked)} /> Allow sensitive facts
          </label>
          <button type="submit" disabled={submitting || !contactId}>
            {submitting ? "Generating..." : "Generate draft"}
          </button>
        </div>
      </form>

      {contextError && <p className="muted">{contextError}</p>}
      {error && <p className="muted">{error}</p>}
      {message && <p className="muted">{message}</p>}
      {result && (
        <article className="card">
          <p className="label">Tone</p>
          <p className="value">{result.tone_band}</p>
          {result.context_summary && (
            <p className="muted">
              Context used: {result.context_summary.recent_interactions ?? 0} recent interactions,{" "}
              {result.context_summary.relevant_chunks ?? 0} email chunks, {result.context_summary.graph_claim_snippets ?? 0} graph claim
              snippets.
            </p>
          )}
          <label className="label" htmlFor="draftSubject">
            Subject
          </label>
          <input
            id="draftSubject"
            value={draftSubject}
            onChange={(e) => setDraftSubject(e.target.value)}
            placeholder="Email subject"
          />

          <label className="label" htmlFor="draftBody">
            Body
          </label>
          <textarea id="draftBody" value={draftBody} onChange={(e) => setDraftBody(e.target.value)} rows={14} />

          <div className="formActionsRow">
            <button type="button" className="btnSecondary" onClick={() => onRevise("edited")} disabled={revising}>
              {revising ? "Saving..." : "Save revision"}
            </button>
            <button type="button" className="btnSecondary" onClick={() => onRevise("approved")} disabled={revising}>
              {revising ? "Saving..." : "Save + approve"}
            </button>
            <button type="button" onClick={onUpdateWritingStyleGuide} disabled={updatingStyle}>
              {updatingStyle ? "Updating style..." : "Update writing style guide"}
            </button>
          </div>
          {result.retrieval_trace && (
            <section>
              <p className="label">Reasoning Trace (Hybrid RAG)</p>
              {result.retrieval_trace.objective_query ? (
                <p className="muted">
                  Retrieval objective: <code>{result.retrieval_trace.objective_query}</code>
                </p>
              ) : null}
              {result.retrieval_trace.recent_interactions && result.retrieval_trace.recent_interactions.length > 0 ? (
                <>
                  <p className="muted">Recent interactions used</p>
                  <div className="citationList">
                    {result.retrieval_trace.recent_interactions.map((interaction, index) => (
                      <article
                        key={`${interaction.interaction_id ?? "interaction"}-${index}`}
                        className="citationItem"
                      >
                        <p className="label">Interaction {index + 1}</p>
                        <p>{interaction.subject ?? "(No subject)"}</p>
                        <p className="muted">
                          id: <code>{interaction.interaction_id ?? "n/a"}</code>
                          {interaction.timestamp ? ` | ${interaction.timestamp}` : ""}
                        </p>
                      </article>
                    ))}
                  </div>
                </>
              ) : null}
              {result.retrieval_trace.graph_claim_snippets && result.retrieval_trace.graph_claim_snippets.length > 0 ? (
                <>
                  <p className="muted">Graph claim snippets used</p>
                  <div className="citationList">
                    {result.retrieval_trace.graph_claim_snippets.map((snippet, index) => (
                      <article key={`${snippet}-${index}`} className="citationItem">
                        <p className="label">Graph snippet {index + 1}</p>
                        <p>{snippet}</p>
                      </article>
                    ))}
                  </div>
                </>
              ) : null}
              {result.retrieval_trace.vector_chunks && result.retrieval_trace.vector_chunks.length > 0 ? (
                <>
                  <p className="muted">Vector chunks used</p>
                  <div className="citationList">
                    {result.retrieval_trace.vector_chunks.map((chunk, index) => (
                      <article key={`${chunk.chunk_id ?? "chunk"}-${index}`} className="citationItem">
                        <p className="label">Chunk {index + 1}</p>
                        <p>{chunk.snippet ?? "No snippet available."}</p>
                        <p className="muted">
                          chunk: <code>{chunk.chunk_id ?? "n/a"}</code> | interaction:{" "}
                          <code>{chunk.interaction_id ?? "n/a"}</code>
                          {typeof chunk.score === "number" ? ` | score: ${chunk.score.toFixed(3)}` : ""}
                        </p>
                      </article>
                    ))}
                  </div>
                </>
              ) : null}
            </section>
          )}
        </article>
      )}
    </section>
  );
}
