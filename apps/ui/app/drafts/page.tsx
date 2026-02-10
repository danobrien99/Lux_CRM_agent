"use client";

import { FormEvent, useState } from "react";

import { apiPost } from "@/lib/api";

type DraftResponse = {
  draft_id: string;
  contact_id: string;
  tone_band: string;
  draft_text: string;
  citations_json: Array<{ paragraph: number; chunk_id: string }>;
  status: string;
};

export default function DraftsPage() {
  const [contactId, setContactId] = useState("");
  const [objective, setObjective] = useState("Reconnect on current priorities");
  const [allowSensitive, setAllowSensitive] = useState(false);
  const [result, setResult] = useState<DraftResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const response = await apiPost<DraftResponse>("/drafts", {
        contact_id: contactId,
        objective,
        allow_sensitive: allowSensitive,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }

  return (
    <section>
      <h1 className="sectionTitle">Draft Composer</h1>
      <form className="card" onSubmit={onSubmit}>
        <label className="label" htmlFor="contactId">
          Contact ID
        </label>
        <input id="contactId" value={contactId} onChange={(e) => setContactId(e.target.value)} placeholder="UUID" required />

        <label className="label" htmlFor="objective">
          Objective
        </label>
        <input id="objective" value={objective} onChange={(e) => setObjective(e.target.value)} />

        <label>
          <input type="checkbox" checked={allowSensitive} onChange={(e) => setAllowSensitive(e.target.checked)} /> Allow sensitive facts
        </label>

        <button type="submit">Generate draft</button>
      </form>

      {error && <p className="muted">{error}</p>}
      {result && (
        <article className="card">
          <p className="label">Tone</p>
          <p className="value">{result.tone_band}</p>
          <pre>{result.draft_text}</pre>
          <p className="muted">Citations: {result.citations_json.length}</p>
        </article>
      )}
    </section>
  );
}
