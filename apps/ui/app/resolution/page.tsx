import { apiGet } from "@/lib/api";
import { ResolutionQueue, type ResolutionTask } from "@/components/resolution-queue";

type TaskResponse = { tasks: ResolutionTask[] };

async function getTasks(): Promise<TaskResponse | null> {
  try {
    return await apiGet<TaskResponse>("/resolution/tasks?status=open");
  } catch {
    return null;
  }
}

export default async function ResolutionPage({ searchParams }: { searchParams?: { contactId?: string } }) {
  const data = await getTasks();

  return (
    <section>
      <h1 className="sectionTitle">Resolution Queue</h1>
      {!data && <p className="muted">Unable to load tasks.</p>}
      {data && <ResolutionQueue initialTasks={data.tasks} focusContactId={searchParams?.contactId ?? null} />}
    </section>
  );
}
