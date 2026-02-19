"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import type { ScoreItem } from "@/components/priority-contacts-browser";

type OpportunityStage = "interact" | "propose" | "close" | "contracting" | "win";

type Opportunity = {
  id: string;
  title: string;
  company: string;
  description: string;
  image: string;
  contacts: ScoreItem[];
  stage: OpportunityStage;
  totalValue: number;
  strategicValueScore: number;
  likelihoodScore: number;
  strategicPriorityScore: number;
};

type StageFilter = OpportunityStage | "all";

const OPPORTUNITY_IMAGES = [
  "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=1200&q=80",
  "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=1400&q=80",
  "https://images.unsplash.com/photo-1552664730-d307ca884978?auto=format&fit=crop&w=1200&q=80",
  "https://images.unsplash.com/photo-1551836022-d5d88e9218df?auto=format&fit=crop&w=1200&q=80",
  "https://images.unsplash.com/photo-1497215728101-856f4ea42174?auto=format&fit=crop&w=1400&q=80",
  "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1200&q=80",
] as const;

const STAGE_LABEL: Record<OpportunityStage, string> = {
  interact: "Interaction Track",
  propose: "Proposal Track",
  close: "Closing Track",
  contracting: "Contracting Track",
  win: "Won Track",
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function contactLabel(item: ScoreItem): string {
  const displayName = item.display_name?.trim();
  if (displayName) {
    return displayName;
  }
  if (item.primary_email) {
    return item.primary_email;
  }
  return "Unknown contact";
}

function normalizeTextLine(value: string): string {
  return value.replace(/^stub:\s*/i, "").replace(/\s+/g, " ").trim();
}

function inferOpportunityStage(sourceText: string, likelihoodScore: number): OpportunityStage {
  const normalized = sourceText.toLowerCase();
  if (normalized.includes("won") || normalized.includes("closed won") || normalized.includes("signed")) {
    return "win";
  }
  if (
    normalized.includes("contract") ||
    normalized.includes("msa") ||
    normalized.includes("procurement") ||
    normalized.includes("legal")
  ) {
    return "contracting";
  }
  if (normalized.includes("close") || normalized.includes("closing") || likelihoodScore >= 86) {
    return "close";
  }
  if (normalized.includes("proposal") || normalized.includes("pricing") || normalized.includes("quote")) {
    return "propose";
  }
  return "interact";
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

function makeOpportunityId(company: string, index: number): string {
  const slug = company
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${slug || "opportunity"}-${index}`;
}

function buildOpportunities(items: ScoreItem[]): Opportunity[] {
  const byCompany = new Map<string, ScoreItem[]>();
  for (const item of items) {
    const company = item.company?.trim() || "Independent Network";
    const companyRows = byCompany.get(company) ?? [];
    companyRows.push(item);
    byCompany.set(company, companyRows);
  }

  return Array.from(byCompany.entries())
    .map(([company, rows], index) => {
      const contacts = [...rows].sort((a, b) => b.priority_score - a.priority_score);
      const avgPriority = contacts.reduce((sum, row) => sum + row.priority_score, 0) / contacts.length;
      const avgRelationship = contacts.reduce((sum, row) => sum + row.relationship_score, 0) / contacts.length;
      const totalValue = Math.round(
        contacts.reduce((sum, row) => sum + row.priority_score * 5200 + row.relationship_score * 1800, 0)
      );

      const strategicValueScore = clamp(Math.round(avgRelationship * 0.65 + Math.min(24, contacts.length * 6)), 1, 100);
      const likelihoodScore = clamp(Math.round(avgPriority * 0.75 + Math.min(22, contacts.length * 5)), 1, 100);
      const valueScore = clamp(Math.round((totalValue / 950000) * 100), 1, 100);
      const strategicPriorityScore = clamp(
        Math.round(valueScore * 0.4 + strategicValueScore * 0.3 + likelihoodScore * 0.3),
        1,
        100
      );

      const primaryContact = contacts[0];
      const primaryContactName = primaryContact ? contactLabel(primaryContact) : "primary contact";
      const description =
        normalizeTextLine(primaryContact?.reasons[0]?.summary ?? "") ||
        normalizeTextLine(primaryContact?.why_now ?? "") ||
        `${company} opportunity has ${contacts.length} linked priority contacts.`;
      const stage = inferOpportunityStage(
        `${primaryContact?.why_now ?? ""} ${primaryContact?.reasons.map((reason) => reason.summary).join(" ") ?? ""}`,
        likelihoodScore
      );

      return {
        id: makeOpportunityId(company, index + 1),
        title: `${company}: ${STAGE_LABEL[stage]} with ${primaryContactName}`,
        company,
        description,
        image: OPPORTUNITY_IMAGES[index % OPPORTUNITY_IMAGES.length],
        contacts,
        stage,
        totalValue,
        strategicValueScore,
        likelihoodScore,
        strategicPriorityScore,
      };
    })
    .sort((a, b) => b.strategicPriorityScore - a.strategicPriorityScore);
}

export function PriorityOpportunities({ items }: { items: ScoreItem[] }) {
  const opportunities = useMemo(() => buildOpportunities(items), [items]);
  const [selectedOpportunityId, setSelectedOpportunityId] = useState<string | null>(null);
  const [stageFilter, setStageFilter] = useState<StageFilter>("all");
  const [pageSize, setPageSize] = useState<number>(6);
  const [page, setPage] = useState<number>(1);

  const filteredOpportunities = useMemo(() => {
    if (stageFilter === "all") {
      return opportunities;
    }
    return opportunities.filter((opportunity) => opportunity.stage === stageFilter);
  }, [opportunities, stageFilter]);

  const safePageSize = Math.max(3, pageSize);
  const totalPages = Math.max(1, Math.ceil(filteredOpportunities.length / safePageSize));
  const currentPage = Math.min(page, totalPages);
  const start = (currentPage - 1) * safePageSize;
  const end = start + safePageSize;
  const pageItems = filteredOpportunities.slice(start, end);

  const selectedOpportunity =
    pageItems.find((opportunity) => opportunity.id === selectedOpportunityId) ?? pageItems[0] ?? null;

  if (opportunities.length === 0) {
    return <p className="muted">No opportunities could be derived from current contact scoring data.</p>;
  }

  return (
    <>
      <article className="card">
        <div className="controlsGrid">
          <div className="controlGroup">
            <label className="label" htmlFor="opportunity-stage-filter">
              Stage
            </label>
            <select
              id="opportunity-stage-filter"
              value={stageFilter}
              onChange={(event) => {
                setStageFilter(event.target.value as StageFilter);
                setPage(1);
                setSelectedOpportunityId(null);
              }}
            >
              <option value="all">All Stages</option>
              <option value="interact">interact</option>
              <option value="propose">propose</option>
              <option value="close">close</option>
              <option value="contracting">contracting</option>
              <option value="win">win</option>
            </select>
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="opportunity-page-size">
              Opportunities Per Page
            </label>
            <input
              id="opportunity-page-size"
              type="number"
              min={3}
              step={1}
              value={String(safePageSize)}
              onChange={(event) => {
                const raw = Number(event.target.value);
                const normalized = Number.isFinite(raw) ? Math.max(3, Math.floor(raw)) : 3;
                setPageSize(normalized);
                setPage(1);
                setSelectedOpportunityId(null);
              }}
            />
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="opportunity-page-select">
              Page
            </label>
            <select
              id="opportunity-page-select"
              value={String(currentPage)}
              onChange={(event) => {
                setPage(Number(event.target.value));
                setSelectedOpportunityId(null);
              }}
            >
              {Array.from({ length: totalPages }, (_unused, index) => index + 1).map((option) => (
                <option key={option} value={option}>
                  {option} / {totalPages}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="pagerRow">
          <button
            className="btnSecondary"
            onClick={() => {
              setPage((previous) => Math.max(1, previous - 1));
              setSelectedOpportunityId(null);
            }}
            disabled={currentPage <= 1}
          >
            Previous
          </button>
          <p className="muted">
            Showing {filteredOpportunities.length === 0 ? 0 : start + 1}-{Math.min(end, filteredOpportunities.length)} of{" "}
            {filteredOpportunities.length}
          </p>
          <button
            className="btnSecondary"
            onClick={() => {
              setPage((previous) => Math.min(totalPages, previous + 1));
              setSelectedOpportunityId(null);
            }}
            disabled={currentPage >= totalPages}
          >
            Next
          </button>
        </div>
      </article>

      {filteredOpportunities.length === 0 && <p className="muted">No opportunities match the selected stage filter.</p>}

      <div className="opportunityGrid">
        {pageItems.map((opportunity) => {
          const isSelected = selectedOpportunity?.id === opportunity.id;
          return (
            <article key={opportunity.id} className="opportunityTile">
              <button
                type="button"
                className={`opportunityTileButton${isSelected ? " isSelected" : ""}`}
                onClick={() => setSelectedOpportunityId(opportunity.id)}
                style={{
                  backgroundImage: `linear-gradient(rgba(0, 0, 0, 0.38), rgba(0, 0, 0, 0.84)), url("${opportunity.image}")`,
                }}
              >
                <div className="opportunityTileTopRow">
                  <p className="opportunityStageBadge">{opportunity.stage}</p>
                  <p className="opportunityPriorityPill">Strategic Priority {opportunity.strategicPriorityScore}</p>
                </div>
                <h3>{opportunity.title}</h3>
                <p className="opportunityDescription">{opportunity.description}</p>
                <p className="opportunityLinkedContacts">
                  Linked contacts: {opportunity.contacts.slice(0, 4).map((contact) => contactLabel(contact)).join(", ")}
                </p>
                <div className="opportunityMetricRow">
                  <span>Total Value {formatCurrency(opportunity.totalValue)}</span>
                  <span>Strategic Value {opportunity.strategicValueScore}</span>
                  <span>Close Likelihood {opportunity.likelihoodScore}%</span>
                </div>
              </button>
            </article>
          );
        })}
      </div>

      {selectedOpportunity && (
        <article className="opportunityContactsPanel">
          <div className="cardHeaderRow">
            <div>
              <p className="sectionEyebrow">Opportunity Contacts</p>
              <h3 className="opportunityContactsPanelTitle">{selectedOpportunity.title}</h3>
            </div>
          </div>
          <p className="muted">
            Stage: {selectedOpportunity.stage} | Strategic Priority: {selectedOpportunity.strategicPriorityScore} | Total Value:{" "}
            {formatCurrency(selectedOpportunity.totalValue)}
          </p>
          <p>{selectedOpportunity.description}</p>

          <div className="opportunityContactsGrid">
            {selectedOpportunity.contacts.map((contact) => (
              <article key={contact.contact_id} className="opportunityContactCard">
                <div className="label">Contact</div>
                <div className="value">{contactLabel(contact)}</div>
                <p className="muted">Company: {contact.company?.trim() || selectedOpportunity.company}</p>
                <p className="muted">Email: {contact.primary_email || "Not available"}</p>
                <div className="opportunityContactMetrics">
                  <span>Priority {contact.priority_score.toFixed(1)}</span>
                  <span>Relationship {contact.relationship_score.toFixed(1)}</span>
                </div>
                <p>{normalizeTextLine(contact.why_now)}</p>
                {contact.reasons[0] && <p className="muted">{normalizeTextLine(contact.reasons[0].summary)}</p>}
                <div className="actionsRow">
                  <Link href={`/contact/${contact.contact_id}`}>Open contact</Link>
                </div>
              </article>
            ))}
          </div>
        </article>
      )}
    </>
  );
}
