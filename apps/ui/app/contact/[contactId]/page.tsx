import Link from "next/link";

import { apiGet } from "@/lib/api";
import RefreshSummaryButton from "./refresh-summary-button";

type ScoreComponentBreakdown = {
  relationship: Record<string, unknown>;
  priority: Record<string, unknown>;
};

type InteractionSummary = {
  total_interactions: number;
  interaction_count_30d: number;
  interaction_count_90d: number;
  inbound_count: number;
  outbound_count: number;
  last_interaction_at: string | null;
  last_subject: string | null;
  recent_subjects: string[];
  recent_topics?: string[];
  priority_next_step?: string | null;
  summary_source?: string | null;
  priority_next_step_source?: string | null;
  brief: string;
};

type ContactScore = {
  contact_id: string;
  profile: {
    contact_id: string;
    display_name: string | null;
    primary_email: string | null;
    owner_user_id: string | null;
    company: string | null;
  } | null;
  interaction_summary: InteractionSummary | null;
  score_components: ScoreComponentBreakdown | null;
  trend: Array<{
    asof: string;
    relationship_score: number;
    priority_score: number;
    components: Array<Record<string, unknown>>;
  }>;
  current: {
    display_name?: string;
    primary_email?: string;
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

function titleCase(value: string): string {
  if (!value) {
    return value;
  }
  return value[0].toUpperCase() + value.slice(1).toLowerCase();
}

function inferNameFromEmail(email: string | null | undefined): string | null {
  const normalized = email?.trim().toLowerCase();
  if (!normalized || !normalized.includes("@")) {
    return null;
  }
  const local = normalized.split("@", 1)[0] ?? "";
  const tokens = local
    .replace(/[0-9]+/g, " ")
    .replace(/[._+-]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);

  if (tokens.length < 2) {
    return null;
  }
  return tokens.map((token) => titleCase(token)).join(" ");
}

type MetricSpec = {
  keys: string[];
  label: string;
  precision?: number;
};

const relationshipMetricSpecs: MetricSpec[] = [
  { keys: ["days_since_last"], label: "Days Since Last Interaction", precision: 0 },
  { keys: ["interaction_count_30d"], label: "Interactions (Last 30 Days)", precision: 0 },
  { keys: ["interaction_count_90d"], label: "Interactions (Last 90 Days)", precision: 0 },
  { keys: ["trailing_31_90"], label: "Interactions (Days 31-90)", precision: 0 },
  { keys: ["recency"], label: "Recency Component (0-45)", precision: 1 },
  { keys: ["frequency"], label: "Frequency Component (0-45)", precision: 1 },
  { keys: ["warmth"], label: "Warmth Component (-10 to 10)", precision: 1 },
  { keys: ["depth"], label: "Depth Component (0-10)", precision: 1 },
  { keys: ["warmth_depth_source_label"], label: "Warmth/Depth Method" },
  { keys: ["heuristic_warmth_delta"], label: "Heuristic Warmth Delta", precision: 1 },
  { keys: ["heuristic_depth_count"], label: "Heuristic Depth Count", precision: 0 },
];

const priorityMetricSpecs: MetricSpec[] = [
  { keys: ["relationship_component"], label: "Relationship Contribution", precision: 1 },
  { keys: ["inactivity_component", "inactivity"], label: "Inactivity Contribution", precision: 1 },
  { keys: ["inactivity_days"], label: "Days Since Last Interaction (Inactivity Input)", precision: 0 },
  { keys: ["open_loop_count"], label: "Open Loop Count", precision: 0 },
  { keys: ["open_loops"], label: "Open Loop Contribution", precision: 1 },
  { keys: ["trigger_score"], label: "Raw Trigger Score", precision: 1 },
  { keys: ["triggers"], label: "Trigger Contribution", precision: 1 },
  { keys: ["last_interaction_id"], label: "Last Interaction ID" },
];

function valueForSpec(values: Record<string, unknown>, spec: MetricSpec): unknown {
  for (const key of spec.keys) {
    const value = values[key];
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }
  return undefined;
}

function formatMetricValue(value: unknown, precision = 1): string {
  if (typeof value === "number") {
    return value.toFixed(precision);
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "string") {
    return value;
  }
  if (value && typeof value === "object") {
    return JSON.stringify(value);
  }
  return "n/a";
}

function contactName(data: ContactScore): string {
  const displayName = data.profile?.display_name?.trim() ?? data.current?.display_name?.trim();
  if (displayName) {
    return displayName;
  }
  const inferredName = inferNameFromEmail(data.profile?.primary_email ?? data.current?.primary_email);
  if (inferredName) {
    return inferredName;
  }
  return "Unknown contact";
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
      <h1 className="sectionTitle">{contactName(data)}</h1>
      <p>
        <Link href="/">Back to Priority Contacts</Link>
      </p>
      <article className="card">
        <div className="cardHeaderRow">
          <div className="label">Contact Profile</div>
          <RefreshSummaryButton
            contactId={data.contact_id}
            className="btnSecondary interactionSummaryDraftLink"
          />
        </div>
        <div className="grid">
          <div>
            <div className="label">Name</div>
            <div className="value">{contactName(data)}</div>
          </div>
          <div>
            <div className="label">Email</div>
            <div className="value">{data.profile?.primary_email ?? data.current?.primary_email ?? "Not available"}</div>
          </div>
          <div>
            <div className="label">Company</div>
            <div className="value">{data.profile?.company ?? "Not available"}</div>
          </div>
          <div>
            <div className="label">Owner User</div>
            <div className="value">{data.profile?.owner_user_id ?? "Not available"}</div>
          </div>
        </div>
        <p className="muted">Contact ID: {data.contact_id}</p>
        {data.current && (
          <>
            <p>Relationship score: {data.current.relationship_score.toFixed(1)}</p>
            <p>Priority score: {data.current.priority_score.toFixed(1)}</p>
            <p className="muted">{data.current.why_now}</p>
          </>
        )}
      </article>

      <article className="card">
        <div className="cardHeaderRow">
          <h2>Interaction Summary</h2>
          <Link
            className="btnSecondary interactionSummaryDraftLink"
            href={{ pathname: "/drafts", query: { contactId: data.contact_id } }}
          >
            Generate Draft
          </Link>
        </div>
        {data.interaction_summary && (
          <>
            {(data.interaction_summary.recent_topics?.length ?? 0) > 0 && (
              <p>
                <span className="label">Recent Topics</span>: {data.interaction_summary.recent_topics!.join(" | ")}
              </p>
            )}
            {(data.interaction_summary.recent_topics?.length ?? 0) === 0 &&
              data.interaction_summary.recent_subjects.length > 0 && (
                <p>
                  <span className="label">Recent Topics</span>: {data.interaction_summary.recent_subjects.join(" | ")}
                </p>
              )}
            <p>{data.interaction_summary.brief}</p>
            {data.interaction_summary.priority_next_step && (
              <p>
                <span className="label">Priority Next Step</span>: {data.interaction_summary.priority_next_step}
              </p>
            )}
            <div className="grid">
              <div>
                <div className="label">Total Interactions</div>
                <div className="value">{data.interaction_summary.total_interactions}</div>
              </div>
              <div>
                <div className="label">In Last 30 Days</div>
                <div className="value">{data.interaction_summary.interaction_count_30d}</div>
              </div>
              <div>
                <div className="label">In Last 90 Days</div>
                <div className="value">{data.interaction_summary.interaction_count_90d}</div>
              </div>
              <div>
                <div className="label">Inbound / Outbound</div>
                <div className="value">
                  {data.interaction_summary.inbound_count} / {data.interaction_summary.outbound_count}
                </div>
              </div>
            </div>
            <p className="muted">
              Last interaction: {data.interaction_summary.last_interaction_at ?? "n/a"}
              {data.interaction_summary.last_subject ? ` | Subject: ${data.interaction_summary.last_subject}` : ""}
            </p>
            {data.interaction_summary.summary_source && (
              <p className="muted">
                Summary source: {data.interaction_summary.summary_source}
                {data.interaction_summary.priority_next_step_source
                  ? ` | Next step source: ${data.interaction_summary.priority_next_step_source}`
                  : ""}
              </p>
            )}
          </>
        )}
      </article>

      <article className="card">
        <h2>Score Components</h2>
        {!data.score_components && <p className="muted">No score component breakdown available.</p>}
        {data.score_components && (
          <div className="grid">
            <div>
              <div className="label">Relationship Score Inputs</div>
              {relationshipMetricSpecs.map((spec) => {
                const value = valueForSpec(data.score_components!.relationship, spec);
                if (value === undefined) {
                  return null;
                }
                return (
                  <p key={`relationship-${spec.keys[0]}`}>
                    {spec.label}: {formatMetricValue(value, spec.precision ?? 1)}
                  </p>
                );
              })}
            </div>
            <div>
              <div className="label">Priority Score Inputs</div>
              {priorityMetricSpecs.map((spec) => {
                const value = valueForSpec(data.score_components!.priority, spec);
                if (value === undefined) {
                  return null;
                }
                return (
                  <p key={`priority-${spec.keys[0]}`}>
                    {spec.label}: {formatMetricValue(value, spec.precision ?? 1)}
                  </p>
                );
              })}
            </div>
          </div>
        )}
      </article>

      <article className="card">
        <h2>Score Trend</h2>
        {data.trend.length === 0 && <p className="muted">No trend points available yet.</p>}
        {data.trend.length > 0 && (
          <div className="grid">
            {data.trend.map((point, index) => {
              const previous = index > 0 ? data.trend[index - 1] : null;
              const relationshipDelta = previous ? point.relationship_score - previous.relationship_score : null;
              const priorityDelta = previous ? point.priority_score - previous.priority_score : null;

              return (
                <article key={point.asof}>
                  <p className="label">{point.asof}</p>
                  <p>
                    Relationship: {point.relationship_score.toFixed(1)}
                    {relationshipDelta !== null ? ` (${relationshipDelta >= 0 ? "+" : ""}${relationshipDelta.toFixed(1)})` : ""}
                  </p>
                  <p>
                    Priority: {point.priority_score.toFixed(1)}
                    {priorityDelta !== null ? ` (${priorityDelta >= 0 ? "+" : ""}${priorityDelta.toFixed(1)})` : ""}
                  </p>
                </article>
              );
            })}
          </div>
        )}
      </article>

      <div className="actionsRow">
        <Link href={{ pathname: "/drafts", query: { contactId: data.contact_id } }}>Generate Draft</Link>
        <Link href="/">Back to Priority Contacts</Link>
      </div>
    </section>
  );
}
