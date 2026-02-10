"use client";

import { FormEvent, useState } from "react";

import { apiPost } from "@/lib/api";

type Match = {
  contact_id: string;
  display_name: string | null;
  match_score: number;
  reason_chain: Array<{ summary: string }>;
};

type MatchResponse = {
  title: string;
  matches: Match[];
};

export default function NewsPage() {
  const [title, setTitle] = useState("Company expansion update");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<MatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await apiPost<MatchResponse>("/news/match", {
        title,
        body_plain: body,
        url: null,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h1 className="sectionTitle">News Matching</h1>
      <form className="card" onSubmit={onSubmit}>
        <label className="label" htmlFor="title">
          Title
        </label>
        <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <label className="label" htmlFor="body">
          Article body
        </label>
        <textarea id="body" rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
        <button type="submit" disabled={loading}>
          {loading ? "Matching..." : "Match contacts"}
        </button>
      </form>

      {error && <p className="muted">{error}</p>}

      {result && (
        <article className="card">
          <h2>{result.title}</h2>
          {result.matches.map((match) => (
            <p key={match.contact_id}>
              {(match.display_name ?? match.contact_id) + " - " + match.match_score.toFixed(2)} ({match.reason_chain[0]?.summary})
            </p>
          ))}
        </article>
      )}
    </section>
  );
}
