import { apiGet } from "@/lib/api";
import { CasesBoard, type CaseContactItem, type CaseOpportunityItem } from "@/components/cases-board";

type CaseContactsResponse = { items: CaseContactItem[] };
type CaseOpportunitiesResponse = { items: CaseOpportunityItem[] };

async function getCaseContacts(): Promise<CaseContactItem[] | null> {
  try {
    const response = await apiGet<CaseContactsResponse>("/cases/contacts?status=open");
    return response.items ?? [];
  } catch {
    return null;
  }
}

async function getCaseOpportunities(): Promise<CaseOpportunityItem[] | null> {
  try {
    const response = await apiGet<CaseOpportunitiesResponse>("/cases/opportunities?status=open");
    return response.items ?? [];
  } catch {
    return null;
  }
}

export default async function CasesPage({ searchParams }: { searchParams?: { focusCaseId?: string; contactId?: string } }) {
  const [contacts, opportunities] = await Promise.all([getCaseContacts(), getCaseOpportunities()]);

  if (!contacts || !opportunities) {
    return (
      <section>
        <h1 className="sectionTitle">Cases</h1>
        <p className="muted">Unable to load provisional contact/opportunity cases.</p>
      </section>
    );
  }

  return (
    <section>
      <h1 className="sectionTitle">Cases</h1>
      <p className="muted">Review and promote provisional contacts and opportunities created from interaction extraction.</p>
      <CasesBoard
        initialContacts={contacts}
        initialOpportunities={opportunities}
        focusCaseId={searchParams?.focusCaseId ?? null}
        focusContactId={searchParams?.contactId ?? null}
      />
    </section>
  );
}
