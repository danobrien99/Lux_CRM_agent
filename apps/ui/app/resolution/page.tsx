import { apiGet } from "@/lib/api";

type Task = {
  task_id: string;
  contact_id: string;
  task_type: string;
  status: string;
};

type TaskResponse = {
  tasks: Task[];
};

async function getTasks(): Promise<TaskResponse | null> {
  try {
    return await apiGet<TaskResponse>("/resolution/tasks?status=open");
  } catch {
    return null;
  }
}

export default async function ResolutionPage() {
  const data = await getTasks();

  return (
    <section>
      <h1 className="sectionTitle">Resolution Queue</h1>
      {!data && <p className="muted">Unable to load tasks.</p>}
      {data && data.tasks.length === 0 && <p className="muted">No open tasks.</p>}
      {data?.tasks.map((task) => (
        <article className="card" key={task.task_id}>
          <p className="label">Task</p>
          <p className="value">{task.task_type}</p>
          <p>Contact: {task.contact_id || "unresolved"}</p>
          <p>Status: {task.status}</p>
        </article>
      ))}
    </section>
  );
}
