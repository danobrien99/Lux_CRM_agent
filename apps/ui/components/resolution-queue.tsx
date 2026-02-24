"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";

export type ResolutionTask = {
  task_id: string;
  contact_id: string;
  task_type: string;
  proposed_claim_id: string;
  current_claim_id?: string | null;
  payload_json: Record<string, unknown>;
  status: string;
};

type ResolveResponse = { task_id: string; status: string };
type Action = "accept_proposed" | "reject_proposed" | "edit_and_accept";

type Message = { kind: "success" | "error" | "info"; text: string };

function proposedValueFromTask(task: ResolutionTask): Record<string, unknown> | null {
  const payload = task.payload_json || {};
  const proposedClaim = payload.proposed_claim;
  if (!proposedClaim || typeof proposedClaim !== "object") {
    return null;
  }
  const value = (proposedClaim as Record<string, unknown>).value_json;
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function helperSummary(task: ResolutionTask): string | null {
  const payload = task.payload_json || {};
  const summary = typeof payload.summary === "string" ? payload.summary : null;
  if (summary && summary.trim()) {
    return summary;
  }
  if (task.task_type === "speaker_identity_resolution") {
    const speakerName = typeof payload.speaker_name === "string" ? payload.speaker_name : "Unknown speaker";
    const matchMethod = typeof payload.match_method === "string" ? payload.match_method : "unresolved";
    const candidates = Array.isArray(payload.candidates) ? payload.candidates.length : 0;
    return `${speakerName} (${matchMethod}, ${candidates} candidate${candidates === 1 ? "" : "s"})`;
  }
  if (task.task_type.endsWith("_discrepancy")) {
    const proposed = payload.proposed_claim as Record<string, unknown> | undefined;
    const current = payload.current_claim as Record<string, unknown> | undefined;
    const proposedType = typeof proposed?.claim_type === "string" ? proposed.claim_type : "claim";
    const currentType = typeof current?.claim_type === "string" ? current.claim_type : "claim";
    return `${currentType} -> ${proposedType} discrepancy requires review`;
  }
  return null;
}

function evidenceCount(task: ResolutionTask): number {
  const payload = task.payload_json || {};
  const refs = payload.evidence_refs;
  if (!refs || typeof refs !== "object") {
    return 0;
  }
  const refsObj = refs as Record<string, unknown>;
  const currentRefs = refsObj["current"];
  const proposedRefs = refsObj["proposed"];
  const current = Array.isArray(currentRefs) ? currentRefs.length : 0;
  const proposed = Array.isArray(proposedRefs) ? proposedRefs.length : 0;
  return current + proposed;
}

export function ResolutionQueue({ initialTasks, focusContactId }: { initialTasks: ResolutionTask[]; focusContactId: string | null }) {
  const router = useRouter();
  const [tasks, setTasks] = useState(initialTasks);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [editDrafts, setEditDrafts] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<Message | null>(null);

  const filteredTasks = useMemo(() => {
    if (!focusContactId) {
      return tasks;
    }
    return tasks.filter((task) => task.contact_id === focusContactId);
  }, [tasks, focusContactId]);

  async function resolveTask(task: ResolutionTask, action: Action) {
    if (busyTaskId) {
      return;
    }
    setBusyTaskId(task.task_id);
    setMessage(null);
    try {
      let editedValueJson: Record<string, unknown> | null = null;
      if (action === "edit_and_accept") {
        const raw = editDrafts[task.task_id]?.trim();
        if (raw) {
          editedValueJson = JSON.parse(raw) as Record<string, unknown>;
        } else {
          editedValueJson = proposedValueFromTask(task);
        }
      }
      const response = await apiPost<ResolveResponse>(`/resolution/tasks/${encodeURIComponent(task.task_id)}/resolve`, {
        action,
        edited_value_json: editedValueJson,
      });
      setTasks((previous) => previous.filter((item) => item.task_id !== task.task_id));
      setMessage({ kind: "success", text: `Resolved ${response.task_id} (${response.status}).` });
      router.refresh();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : `Resolve failed for ${task.task_id}` });
    } finally {
      setBusyTaskId(null);
    }
  }

  return (
    <>
      {focusContactId ? <p className="muted">Filtered to contact: {focusContactId}</p> : null}
      {message ? <p className={`statusMessage status-${message.kind}`}>{message.text}</p> : null}
      {filteredTasks.length === 0 ? <p className="muted">No open tasks.</p> : null}
      {filteredTasks.map((task) => {
        const defaultEdit = editDrafts[task.task_id] ?? JSON.stringify(proposedValueFromTask(task) ?? {}, null, 2);
        return (
          <article className="card" key={task.task_id}>
            {helperSummary(task) ? <p className="muted">{helperSummary(task)}</p> : null}
            <div className="grid">
              <div>
                <div className="label">Task</div>
                <div className="value">{task.task_type}</div>
              </div>
              <div>
                <div className="label">Contact</div>
                <div className="value">{task.contact_id || "unresolved"}</div>
              </div>
              <div>
                <div className="label">Proposed Claim</div>
                <div className="value">{task.proposed_claim_id}</div>
              </div>
              <div>
                <div className="label">Current Claim</div>
                <div className="value">{task.current_claim_id || "n/a"}</div>
              </div>
              <div>
                <div className="label">Evidence Refs</div>
                <div className="value">{evidenceCount(task)}</div>
              </div>
            </div>

            <details open>
              <summary>Task payload</summary>
              <pre>{JSON.stringify(task.payload_json || {}, null, 2)}</pre>
            </details>

            <div>
              <div className="label">Edit + Accept (JSON value override)</div>
              <textarea
                rows={8}
                value={defaultEdit}
                onChange={(event) =>
                  setEditDrafts((previous) => ({
                    ...previous,
                    [task.task_id]: event.target.value,
                  }))
                }
              />
            </div>

            <div className="actionsRow">
              <button
                type="button"
                className="btnSecondary"
                onClick={() => void resolveTask(task, "accept_proposed")}
                disabled={busyTaskId === task.task_id}
              >
                {busyTaskId === task.task_id ? "Working..." : "Accept Proposed"}
              </button>
              <button
                type="button"
                className="btnSecondary"
                onClick={() => void resolveTask(task, "reject_proposed")}
                disabled={busyTaskId === task.task_id}
              >
                Reject Proposed
              </button>
              <button
                type="button"
                className="btnSecondary"
                onClick={() => void resolveTask(task, "edit_and_accept")}
                disabled={busyTaskId === task.task_id}
              >
                Edit and Accept
              </button>
            </div>
          </article>
        );
      })}
    </>
  );
}
