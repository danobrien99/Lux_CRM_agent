"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { apiDelete } from "@/lib/api";

type ScoreReason = {
  summary: string;
};

export type ScoreItem = {
  contact_id: string;
  display_name: string | null;
  primary_email: string | null;
  company: string | null;
  relationship_score: number;
  priority_score: number;
  why_now: string;
  reasons: ScoreReason[];
};

type DeleteContactResponse = {
  contact_id: string;
  deleted: boolean;
  graph_deleted?: boolean;
  graph_delete_error?: string | null;
};

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

type SortField = "priority" | "relationship";
type SortDirection = "desc" | "asc";
type MessageKind = "success" | "error" | "info";

type UiMessage = {
  kind: MessageKind;
  text: string;
};

function titleCase(value: string): string {
  if (!value) {
    return value;
  }
  return value[0].toUpperCase() + value.slice(1).toLowerCase();
}

function inferNameFromEmail(email: string | null): string | null {
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

function contactLabel(item: ScoreItem): string {
  const displayName = item.display_name?.trim();
  if (displayName) {
    return displayName;
  }
  const inferredName = inferNameFromEmail(item.primary_email);
  if (inferredName) {
    return inferredName;
  }
  return "Unknown contact";
}

function companyLabel(item: ScoreItem): string {
  const company = item.company?.trim();
  return company ? company : "Not available";
}

function compareItems(a: ScoreItem, b: ScoreItem, field: SortField, direction: SortDirection): number {
  const factor = direction === "desc" ? -1 : 1;
  if (field === "priority") {
    return (a.priority_score - b.priority_score) * factor;
  }
  return (a.relationship_score - b.relationship_score) * factor;
}

export function PriorityContactsBrowser({ items }: { items: ScoreItem[] }) {
  const [records, setRecords] = useState(items);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortField, setSortField] = useState<SortField>("priority");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);
  const [deletingContactId, setDeletingContactId] = useState<string | null>(null);
  const [message, setMessage] = useState<UiMessage | null>(null);

  useEffect(() => {
    setRecords(items);
  }, [items]);

  const filteredSorted = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const filtered = query
      ? records.filter((item) => contactLabel(item).toLowerCase().includes(query))
      : [...records];
    filtered.sort((a, b) => compareItems(a, b, sortField, sortDirection));
    return filtered;
  }, [records, searchQuery, sortField, sortDirection]);

  const totalPages = Math.max(1, Math.ceil(filteredSorted.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const start = (currentPage - 1) * pageSize;
  const end = start + pageSize;
  const pageItems = filteredSorted.slice(start, end);

  async function handleDelete(item: ScoreItem) {
    if (deletingContactId) {
      return;
    }
    setMessage(null);

    setDeletingContactId(item.contact_id);
    try {
      const response = await apiDelete<DeleteContactResponse>(`/contacts/${item.contact_id}`);
      if (!response.deleted) {
        setMessage({
          kind: "info",
          text: `${contactLabel(item)} was already deleted.`,
        });
        setRecords((previous) => previous.filter((row) => row.contact_id !== item.contact_id));
        return;
      }
      setRecords((previous) => previous.filter((row) => row.contact_id !== item.contact_id));
      setPage(1);
      if (response.graph_deleted === false) {
        setMessage({
          kind: "info",
          text: `Deleted ${contactLabel(item)} from contacts, but graph cleanup is pending.`,
        });
        return;
      }
      setMessage({
        kind: "success",
        text: `Deleted ${contactLabel(item)}.`,
      });
    } catch (error) {
      const details = error instanceof Error ? ` (${error.message})` : "";
      setMessage({
        kind: "error",
        text: `Delete failed for ${contactLabel(item)}${details}`,
      });
    } finally {
      setDeletingContactId(null);
    }
  }

  return (
    <>
      <article className="card">
        <div className="controlsGrid">
          <div className="controlGroup">
            <label className="label" htmlFor="contacts-search">
              Search Name
            </label>
            <input
              id="contacts-search"
              value={searchQuery}
              onChange={(event) => {
                setSearchQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Search by contact name"
            />
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="contacts-sort-field">
              Sort Field
            </label>
            <select
              id="contacts-sort-field"
              value={sortField}
              onChange={(event) => {
                setSortField(event.target.value as SortField);
                setPage(1);
              }}
            >
              <option value="priority">Priority Score</option>
              <option value="relationship">Relationship Score</option>
            </select>
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="contacts-sort-direction">
              Sort Direction
            </label>
            <select
              id="contacts-sort-direction"
              value={sortDirection}
              onChange={(event) => {
                setSortDirection(event.target.value as SortDirection);
                setPage(1);
              }}
            >
              <option value="desc">High to Low</option>
              <option value="asc">Low to High</option>
            </select>
          </div>

          <div className="controlGroup">
            <label className="label" htmlFor="contacts-page-size">
              Contacts Per Page
            </label>
            <select
              id="contacts-page-size"
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
            <label className="label" htmlFor="contacts-page-select">
              Page
            </label>
            <select
              id="contacts-page-select"
              value={String(currentPage)}
              onChange={(event) => setPage(Number(event.target.value))}
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
            onClick={() => setPage((previous) => Math.max(1, previous - 1))}
            disabled={currentPage <= 1}
          >
            Previous
          </button>
          <p className="muted">
            Showing {filteredSorted.length === 0 ? 0 : start + 1}-{Math.min(end, filteredSorted.length)} of{" "}
            {filteredSorted.length}
          </p>
          <button
            className="btnSecondary"
            onClick={() => setPage((previous) => Math.min(totalPages, previous + 1))}
            disabled={currentPage >= totalPages}
          >
            Next
          </button>
        </div>
      </article>

      {message && <p className={`statusMessage status-${message.kind}`}>{message.text}</p>}

      {filteredSorted.length === 0 && <p className="muted">No contacts match your search.</p>}

      {pageItems.map((item) => (
        <article key={item.contact_id} className="card">
          <div className="grid">
            <div>
              <div className="label">Name</div>
              <div className="value">{contactLabel(item)}</div>
            </div>
            <div>
              <div className="label">Company</div>
              <div className="value">{companyLabel(item)}</div>
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
          <p className="muted">Email: {item.primary_email ?? "Not available"}</p>
          <p>{item.why_now}</p>
          {item.reasons[0] && <p className="muted">{item.reasons[0].summary}</p>}
          <div className="actionsRow">
            <Link href={`/contact/${item.contact_id}`}>Open contact</Link>
            <button
              type="button"
              className="btnDangerCompact"
              onClick={() => void handleDelete(item)}
              disabled={deletingContactId === item.contact_id}
            >
              {deletingContactId === item.contact_id ? "Deleting..." : "Delete"}
            </button>
          </div>
        </article>
      ))}
    </>
  );
}
