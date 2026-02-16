import { apiGet } from "@/lib/api";
import { PriorityContactsBrowser, type ScoreItem } from "@/components/priority-contacts-browser";

type ScoreResponse = {
  asof: string;
  items: ScoreItem[];
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

const projectTiles = [
  {
    title: "Board Outreach Program",
    category: "Executive Network",
    shape: "portrait",
    image:
      "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=1000&q=80",
  },
  {
    title: "Investor Re-engagement",
    category: "Capital Partners",
    shape: "landscape",
    image:
      "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=1400&q=80",
  },
  {
    title: "Strategic Introductions",
    category: "Partnerships",
    shape: "square",
    image:
      "https://images.unsplash.com/photo-1552664730-d307ca884978?auto=format&fit=crop&w=1200&q=80",
  },
  {
    title: "Quarterly Relationship Review",
    category: "Portfolio",
    shape: "portrait",
    image:
      "https://images.unsplash.com/photo-1551836022-d5d88e9218df?auto=format&fit=crop&w=1000&q=80",
  },
  {
    title: "Client Renewal Campaign",
    category: "Revenue Accounts",
    shape: "landscape",
    image:
      "https://images.unsplash.com/photo-1497215728101-856f4ea42174?auto=format&fit=crop&w=1400&q=80",
  },
  {
    title: "Founder Advisory Pulse",
    category: "Advisory Board",
    shape: "square",
    image:
      "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1200&q=80",
  },
] as const;

async function getScores(): Promise<ScoreResponse | null> {
  try {
    return await apiGet<ScoreResponse>("/scores/today?limit=500");
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const data = await getScores();

  return (
    <>
      <section className="heroSection">
        <div className="heroInner">
          <p className="heroKicker">Relationship Intelligence Platform</p>
          <h1 className="heroTitle">Luxury-Grade Relationship Operations for High-Value Networks</h1>
          <p className="heroBody">
            LUX combines interaction intelligence, editorial-grade context, and graph-native memory so each conversation feels
            deliberate.
          </p>
          <div className="heroActions">
            <a className="ctaButton" href="#priority-contacts">
              View Priority Dashboard
            </a>
            <a className="ghostButton" href="#recent-projects">
              Explore Recent Projects
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

      <section id="recent-projects" className="projectsSection">
        <p className="sectionEyebrow">Recent Projects</p>
        <h2 className="sectionTitle">Client Portfolio</h2>
        <div className="masonryGrid">
          {projectTiles.map((tile) => (
            <article key={tile.title} className={`projectTile ${tile.shape}`}>
              <img src={tile.image} alt={tile.title} loading="lazy" />
              <div className="projectOverlay">
                <p>{tile.category}</p>
                <h3>{tile.title}</h3>
              </div>
            </article>
          ))}
        </div>
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
