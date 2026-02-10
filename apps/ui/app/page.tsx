import Link from "next/link";

import { apiGet } from "@/lib/api";

type ScoreReason = {
  summary: string;
};

type ScoreItem = {
  contact_id: string;
  display_name: string | null;
  relationship_score: number;
  priority_score: number;
  why_now: string;
  reasons: ScoreReason[];
};

type ScoreResponse = {
  asof: string;
  items: ScoreItem[];
};

async function getScores(): Promise<ScoreResponse | null> {
  try {
    return await apiGet<ScoreResponse>("/scores/today?limit=25");
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const data = await getScores();

  return (
    <section>
      <h1 className="sectionTitle">Priority Contacts</h1>
      {!data && <p className="muted">API unavailable. Start backend on port 8000.</p>}
      {data && data.items.length === 0 && <p className="muted">No contacts scored yet.</p>}
      {data &&
        data.items.map((item) => (
          <article key={item.contact_id} className="card">
            <div className="grid">
              <div>
                <div className="label">Contact</div>
                <div className="value">{item.display_name ?? item.contact_id}</div>
              </div>
              <div>
                <div className="label">Priority</div>
                <div className="value">{item.priority_score.toFixed(1)}</div>
              </div>
              <div>
                <div className="label">Relationship</div>
                <div className="value">{item.relationship_score.toFixed(1)}</div>
              </div>
            </div>
            <p>{item.why_now}</p>
            {item.reasons[0] && <p className="muted">{item.reasons[0].summary}</p>}
            <Link href={`/contact/${item.contact_id}`}>Open contact</Link>
          </article>
        ))}
    </section>
  );
}
