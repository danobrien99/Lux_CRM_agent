"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";

export type CaseContactItem = {
  case_id: string;
  email: string;
  display_name: string | null;
  status: string;
  entity_status: "canonical" | "provisional" | "rejected";
  interaction_id?: string | null;
  provisional_contact_id?: string | null;
  promotion_reason?: string | null;
  gate_results: Record<string, unknown>;
  evidence_count: number;
  created_at?: string | null;
  updated_at?: string | null;
};

export type CaseOpportunityItem = {
  case_id: string;
  title: string;
  company_name?: string | null;
  thread_id?: string | null;
  status: string;
  entity_status: "canonical" | "provisional" | "rejected";
  interaction_id?: string | null;
  promotion_reason?: string | null;
  gate_results: Record<string, unknown>;
  motivators: string[];
  contact_ids: string[];
  created_at?: string | null;
  updated_at?: string | null;
};

type CasePromotionResponse = {
  case_id: string;
  status: string;
  entity_status: string;
  promoted_id?: string | null;
};

type Tab = "contacts" | "opportunities";

type Message = { kind: "success" | "error" | "info"; text: string };

export function CasesBoard({
  initialContacts,
  initialOpportunities,
  focusCaseId,
  focusContactId,
}: {
  initialContacts: CaseContactItem[];
  initialOpportunities: CaseOpportunityItem[];
  focusCaseId: string | null;
  focusContactId: string | null;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>(focusCaseId?.startsWith("case_opp:") ? "opportunities" : "contacts");
  const [contacts, setContacts] = useState(initialContacts);
  const [opportunities, setOpportunities] = useState(initialOpportunities);
  const [busyCaseId, setBusyCaseId] = useState<string | null>(null);
  const [message, setMessage] = useState<Message | null>(null);

  const filteredContacts = useMemo(() => {
    if (!focusContactId) {
      return contacts;
    }
    return contacts.filter((item) => item.provisional_contact_id === focusContactId || item.case_id === focusCaseId);
  }, [contacts, focusContactId, focusCaseId]);

  const filteredOpportunities = useMemo(() => {
    if (!focusContactId && !focusCaseId) {
      return opportunities;
    }
    return opportunities.filter((item) => {
      if (focusCaseId && item.case_id === focusCaseId) {
        return true;
      }
      if (focusContactId && item.contact_ids.includes(focusContactId)) {
        return true;
      }
      return false;
    });
  }, [opportunities, focusCaseId, focusContactId]);

  async function promoteContact(caseId: string) {
    if (busyCaseId) {
      return;
    }
    setBusyCaseId(caseId);
    setMessage(null);
    try {
      const response = await apiPost<CasePromotionResponse>(`/cases/contacts/${encodeURIComponent(caseId)}/promote`, {
        promotion_reason: "manual_promotion_ui",
        gate_results: { source: "cases_ui" },
      });
      if (response.promoted_id) {
        setContacts((previous) => previous.filter((item) => item.case_id !== caseId));
        setMessage({ kind: "success", text: `Promoted contact case ${caseId} -> ${response.promoted_id}.` });
      } else {
        setMessage({ kind: "info", text: `Case ${caseId} was not promoted (status: ${response.status}).` });
      }
      router.refresh();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : `Promote failed for ${caseId}` });
    } finally {
      setBusyCaseId(null);
    }
  }

  async function promoteOpportunity(caseId: string) {
    if (busyCaseId) {
      return;
    }
    setBusyCaseId(caseId);
    setMessage(null);
    try {
      const response = await apiPost<CasePromotionResponse>(`/cases/opportunities/${encodeURIComponent(caseId)}/promote`, {
        promotion_reason: "manual_promotion_ui",
        gate_results: { source: "cases_ui" },
      });
      if (response.promoted_id) {
        setOpportunities((previous) => previous.filter((item) => item.case_id !== caseId));
        setMessage({ kind: "success", text: `Promoted opportunity case ${caseId} -> ${response.promoted_id}.` });
      } else {
        setMessage({ kind: "info", text: `Case ${caseId} was not promoted (status: ${response.status}).` });
      }
      router.refresh();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : `Promote failed for ${caseId}` });
    } finally {
      setBusyCaseId(null);
    }
  }

  return (
    <>
      <article className="card">
        <div className="actionsRow">
          <button type="button" className="btnSecondary" onClick={() => setTab("contacts")}>Contacts ({filteredContacts.length})</button>
          <button type="button" className="btnSecondary" onClick={() => setTab("opportunities")}>Opportunities ({filteredOpportunities.length})</button>
          <Link href="/resolution">Open Resolution Queue</Link>
        </div>
        {focusCaseId || focusContactId ? (
          <p className="muted">
            Filtered view
            {focusCaseId ? ` | Case: ${focusCaseId}` : ""}
            {focusContactId ? ` | Contact: ${focusContactId}` : ""}
          </p>
        ) : null}
      </article>

      {message ? <p className={`statusMessage status-${message.kind}`}>{message.text}</p> : null}

      {tab === "contacts" && (
        <>
          {filteredContacts.length === 0 ? <p className="muted">No open provisional contact cases.</p> : null}
          {filteredContacts.map((item) => (
            <article key={item.case_id} className="card">
              <div className="grid">
                <div>
                  <div className="label">Name</div>
                  <div className="value">{item.display_name || "Unknown"}</div>
                </div>
                <div>
                  <div className="label">Email</div>
                  <div className="value">{item.email}</div>
                </div>
                <div>
                  <div className="label">Evidence</div>
                  <div className="value">{item.evidence_count}</div>
                </div>
                <div>
                  <div className="label">Status</div>
                  <div className="value">{item.status}</div>
                </div>
              </div>
              <p className="muted">Case ID: {item.case_id}</p>
              <p className="muted">Promotion reason: {item.promotion_reason || "n/a"}</p>
              <details>
                <summary>Gate results</summary>
                <pre>{JSON.stringify(item.gate_results || {}, null, 2)}</pre>
              </details>
              <div className="actionsRow">
                {item.provisional_contact_id ? <Link href={`/contact/${item.provisional_contact_id}`}>Open provisional contact</Link> : null}
                <button
                  type="button"
                  className="btnSecondary"
                  onClick={() => void promoteContact(item.case_id)}
                  disabled={busyCaseId === item.case_id}
                >
                  {busyCaseId === item.case_id ? "Promoting..." : "Promote Contact"}
                </button>
              </div>
            </article>
          ))}
        </>
      )}

      {tab === "opportunities" && (
        <>
          {filteredOpportunities.length === 0 ? <p className="muted">No open provisional opportunity cases.</p> : null}
          {filteredOpportunities.map((item) => (
            <article key={item.case_id} className="card">
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
                  <div className="label">Contacts</div>
                  <div className="value">{item.contact_ids.length}</div>
                </div>
                <div>
                  <div className="label">Status</div>
                  <div className="value">{item.status}</div>
                </div>
              </div>
              <p className="muted">Case ID: {item.case_id}</p>
              <p className="muted">Motivators: {item.motivators.length > 0 ? item.motivators.join(" | ") : "none"}</p>
              <p className="muted">Promotion reason: {item.promotion_reason || "n/a"}</p>
              <details>
                <summary>Gate results</summary>
                <pre>{JSON.stringify(item.gate_results || {}, null, 2)}</pre>
              </details>
              <div className="actionsRow">
                {item.contact_ids.slice(0, 3).map((cid) => (
                  <Link key={cid} href={`/contact/${cid}`}>Contact {cid}</Link>
                ))}
                <button
                  type="button"
                  className="btnSecondary"
                  onClick={() => void promoteOpportunity(item.case_id)}
                  disabled={busyCaseId === item.case_id}
                >
                  {busyCaseId === item.case_id ? "Promoting..." : "Promote Opportunity"}
                </button>
              </div>
            </article>
          ))}
        </>
      )}
    </>
  );
}
