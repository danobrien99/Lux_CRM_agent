"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";

type RefreshSummaryButtonProps = {
  contactId: string;
  className?: string;
};

export default function RefreshSummaryButton({ contactId, className }: RefreshSummaryButtonProps) {
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onRefresh() {
    if (!contactId || refreshing) {
      return;
    }
    setRefreshing(true);
    setError(null);
    try {
      await apiPost(`/scores/contact/${encodeURIComponent(contactId)}/refresh_summary`, {});
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refresh summary");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="refreshSummaryControl">
      <button type="button" className={className} onClick={onRefresh} disabled={refreshing}>
        {refreshing ? "Refreshing..." : "Refresh Summary"}
      </button>
      {error ? <p className="muted">{error}</p> : null}
    </div>
  );
}
