"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

export type OpportunityContactProfile = {
  contact_id: string;
  display_name: string | null;
  primary_email: string | null;
  company: string | null;
};

export type OpportunityNextStep = {
  summary: string;
  type: string;
  source: string;
  confidence: number;
  contact_id?: string | null;
  opportunity_id?: string | null;
  case_id?: string | null;
  evidence_refs?: Array<Record<string, unknown>>;
};

export type RankedOpportunity = {
  opportunity_id?: string | null;
  case_id?: string | null;
  title: string;
  company_name?: string | null;
  status: string;
  entity_status: "canonical" | "provisional" | "rejected";
  kind: "opportunity" | "case_opportunity";
  priority_score: number;
  next_step?: OpportunityNextStep | null;
  linked_contacts: OpportunityContactProfile[];
  reason_chain: string[];
  updated_at?: string | null;
  last_engagement_at?: string | null;
  thread_id?: string | null;
};

type KindFilter = "all" | RankedOpportunity["kind"];

const PAGE_SIZE_OPTIONS = [5, 10, 20];

function badgeLabel(item: RankedOpportunity): string {
  if (item.kind === "case_opportunity") {
    return "Provisional Opportunity";
  }
  return item.entity_status === "canonical" ? "Promoted Opportunity" : "Opportunity";
}

function contactLabel(contact: OpportunityContactProfile): string {
  return contact.display_name?.trim() || contact.primary_email || contact.contact_id;
}

export function PriorityOpportunities({ items }: { items: RankedOpportunity[] }) {
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZE_OPTIONS[0]);
  const [page, setPage] = useState<number>(1);

  const filtered = useMemo(() => {
    if (kindFilter === "all") {
      return items;
    }
    return items.filter((item) => item.kind === kindFilter);
  }, [items, kindFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const start = (currentPage - 1) * pageSize;
  const pageItems = filtered.slice(start, start + pageSize);

  if (items.length === 0) {
    return <p className="muted">No real opportunities or provisional opportunity cases are available yet.</p>;
  }

  return (
    <>
      <article className="card">
        <div className="controlsGrid">
          <div className="controlGroup">
            <label className="label" htmlFor="opp-kind-filter">
              Opportunity Type
            </label>
            <select
              id="opp-kind-filter"
              value={kindFilter}
              onChange={(event) => {
                setKindFilter(event.target.value as KindFilter);
                setPage(1);
              }}
            >
              <option value="all">All</option>
              <option value="opportunity">Promoted</option>
              <option value="case_opportunity">Provisional</option>
            </select>
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="opp-page-size">
              Opportunities Per Page
            </label>
            <select
              id="opp-page-size"
              value={String(pageSize)}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
            >
              {PAGE_SIZE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="opp-page">
              Page
            </label>
            <select id="opp-page" value={String(currentPage)} onChange={(event) => setPage(Number(event.target.value))}>
              {Array.from({ length: totalPages }, (_unused, index) => index + 1).map((option) => (
                <option key={option} value={option}>
                  {option} / {totalPages}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="pagerRow">
          <button className="btnSecondary" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={currentPage <= 1}>
            Previous
          </button>
          <p className="muted">
            Showing {filtered.length === 0 ? 0 : start + 1}-{Math.min(start + pageSize, filtered.length)} of {filtered.length}
          </p>
          <button
            className="btnSecondary"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={currentPage >= totalPages}
          >
            Next
          </button>
        </div>
      </article>

      {filtered.length === 0 && <p className="muted">No opportunities match the selected filter.</p>}

      {pageItems.map((item) => {
        const id = item.opportunity_id || item.case_id || item.title;
        const casesHref = item.case_id ? `/cases?focusCaseId=${encodeURIComponent(item.case_id)}` : "/cases";
        return (
          <article key={id} className="card">
            <div className="grid">
              <div>
                <div className="label">Title</div>
                <div className="value">{item.title}</div>
              </div>
              <div>
                <div className="label">Company</div>
                <div className="value">{item.company_name || "Not available"}</div>
              </div>
              <div>
                <div className="label">Priority</div>
                <div className="value">{item.priority_score.toFixed(1)}</div>
              </div>
              <div>
                <div className="label">Type</div>
                <div className="value">{badgeLabel(item)}</div>
              </div>
            </div>

            <p className="muted">
              Status: {item.status} | Entity status: {item.entity_status}
              {item.thread_id ? ` | Thread: ${item.thread_id}` : ""}
            </p>

            {item.next_step?.summary ? (
              <p>
                <span className="label">Suggested Next Step</span>: {item.next_step.summary}
              </p>
            ) : (
              <p className="muted">No next-step suggestion yet.</p>
            )}

            {item.next_step && (
              <p className="muted">
                Source: {item.next_step.source} | Confidence: {item.next_step.confidence.toFixed(2)} | Evidence refs: {item.next_step.evidence_refs?.length ?? 0}
              </p>
            )}

            {item.reason_chain.length > 0 && (
              <p className="muted">
                Why ranked: {item.reason_chain.join(" | ")}
              </p>
            )}

            {item.linked_contacts.length > 0 && (
              <div>
                <div className="label">Linked Contacts</div>
                <div className="actionsRow">
                  {item.linked_contacts.slice(0, 4).map((contact) => (
                    <Link key={contact.contact_id} href={`/contact/${contact.contact_id}`}>
                      {contactLabel(contact)}
                    </Link>
                  ))}
                </div>
              </div>
            )}

            <div className="actionsRow">
              {item.opportunity_id ? <span className="muted">Opportunity ID: {item.opportunity_id}</span> : null}
              {item.case_id ? <Link href={casesHref}>Review provisional opportunity</Link> : null}
              {!item.case_id ? <Link href="/cases">Open Cases</Link> : null}
            </div>
          </article>
        );
      })}
    </>
  );
}
