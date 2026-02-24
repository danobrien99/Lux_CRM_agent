import { apiGet } from "@/lib/api";
import { PriorityOpportunities, type RankedOpportunity } from "@/components/priority-opportunities";
import { PriorityContactsBrowser, type ScoreItem } from "@/components/priority-contacts-browser";

type ScoreResponse = {
  asof: string;
  items: ScoreItem[];
};

type OpportunitiesResponse = {
  asof: string;
  items: RankedOpportunity[];
};

const serviceCards = [
  {
    title: "Relationship Intelligence",
    body: "Continuously prioritize who to reach out to based on interaction depth, recency, and active signals.",
  },
  {
    title: "Contextual Drafting",
    body: "Generate polished outreach drafts grounded in recent correspondence and accepted graph claims.",
  },
  {
    title: "Signal Matching",
    body: "Match external news and evolving events to relevant contacts so outreach remains timely and specific.",
  },
];

async function getScores(): Promise<ScoreResponse | null> {
  try {
    return await apiGet<ScoreResponse>("/scores/today?limit=500");
  } catch {
    return null;
  }
}

async function getOpportunities(): Promise<OpportunitiesResponse | null> {
  try {
    return await apiGet<OpportunitiesResponse>("/scores/opportunities?limit=200");
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const data = await getScores();
  const opportunities = await getOpportunities();

  return (
    <>
      <section className="heroSection">
        <div className="heroInner">
          <p className="heroKicker">Relationship Intelligence Platform</p>
          <h1 className="heroTitle">Strategic Contact Management System.</h1>
          <p className="heroBody">
            LUX CRM - intelligent contact management based on interaction tracking, knowledge extraction, and context memory so
            conversations feel personal and deliberate
          </p>
          <div className="heroActions">
            <a className="ctaButton" href="#priority-contacts">
              View Priority Contacts
            </a>
            <a className="ghostButton" href="#priority-opportunities">
              Explore Priority Opportunities
            </a>
          </div>
        </div>
      </section>

      <section className="servicesSection fullBleedSection">
        <p className="sectionEyebrow">Core Capabilities</p>
        <h2 className="sectionTitle">Service Architecture</h2>
        <div className="serviceGrid">
          {serviceCards.map((card, index) => (
            <article key={card.title} className="serviceCard">
              <p className="serviceIndex">{String(index + 1).padStart(2, "0")}</p>
              <h3>{card.title}</h3>
              <p>{card.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="priority-opportunities" className="projectsSection">
        <p className="sectionEyebrow">Priority Opportunities</p>
        <h2 className="sectionTitle">Priority Opportunities</h2>
        {!opportunities && <p className="muted">API unavailable. Start backend on port 8000.</p>}
        {opportunities && opportunities.items.length === 0 && <p className="muted">No priority opportunities identified yet.</p>}
        {opportunities && opportunities.items.length > 0 && <PriorityOpportunities items={opportunities.items} />}
      </section>

      <section id="priority-contacts" className="dashboardSection">
        <p className="sectionEyebrow">Live CRM Feed</p>
        <h2 className="sectionTitle">Priority Contacts</h2>
        {!data && <p className="muted">API unavailable. Start backend on port 8000.</p>}
        {data && data.items.length === 0 && <p className="muted">No contacts scored yet.</p>}
        {data && <PriorityContactsBrowser items={data.items} />}
      </section>
    </>
  );
}
