import Link from "next/link";

import { apiGet } from "@/lib/api";

type ContactScore = {
  contact_id: string;
  trend: Array<{
    asof: string;
    relationship_score: number;
    priority_score: number;
  }>;
  current: {
    display_name?: string;
    relationship_score: number;
    priority_score: number;
    why_now: string;
  } | null;
};

async function getContact(contactId: string): Promise<ContactScore | null> {
  try {
    return await apiGet<ContactScore>(`/scores/contact/${contactId}`);
  } catch {
    return null;
  }
}

export default async function ContactPage({ params }: { params: { contactId: string } }) {
  const data = await getContact(params.contactId);

  if (!data) {
    return (
      <section>
        <h1 className="sectionTitle">Contact</h1>
        <p className="muted">Unable to load contact score details.</p>
      </section>
    );
  }

  return (
    <section>
      <h1 className="sectionTitle">Contact Detail</h1>
      <article className="card">
        <div className="label">Contact ID</div>
        <div className="value">{data.contact_id}</div>
        {data.current && (
          <>
            <p>Relationship score: {data.current.relationship_score.toFixed(1)}</p>
            <p>Priority score: {data.current.priority_score.toFixed(1)}</p>
            <p className="muted">{data.current.why_now}</p>
          </>
        )}
      </article>

      <article className="card">
        <h2>Score Trend</h2>
        {data.trend.map((point) => (
          <p key={point.asof}>
            {point.asof}: relationship {point.relationship_score.toFixed(1)} | priority {point.priority_score.toFixed(1)}
          </p>
        ))}
      </article>

      <Link href="/drafts">Generate Draft</Link>
    </section>
  );
}
