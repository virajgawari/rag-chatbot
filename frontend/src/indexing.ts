import type { DocumentStatus } from "./types";

export function formatDuration(seconds: number): string {
  const value = Math.max(0, Math.ceil(Number.isFinite(seconds) ? seconds : 0));
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`;
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
}

export function indexingProgress(item: {
  status: DocumentStatus;
  chunksProcessed?: number;
  totalChunks?: number;
  elapsedSeconds?: number | null;
  estimatedRemainingSeconds?: number | null;
}): { label: string; details: string } {
  const labels: Record<DocumentStatus, string> = {
    queued: "Waiting to index…",
    parsing: "Reading document…",
    chunking: "Preparing passages…",
    embedding: "Indexing passages…",
    ready: "Ready",
    failed: "Indexing failed",
  };
  const parts: string[] = [];
  if (item.status === "embedding" && item.totalChunks) {
    parts.push(`${item.chunksProcessed ?? 0} of ${item.totalChunks} chunks`);
  }
  if (item.elapsedSeconds != null) parts.push(`${formatDuration(item.elapsedSeconds)} elapsed`);
  if (item.estimatedRemainingSeconds != null && item.estimatedRemainingSeconds > 0) {
    parts.push(`~${formatDuration(item.estimatedRemainingSeconds)} remaining (estimate)`);
  }
  return { label: labels[item.status], details: parts.join(" · ") };
}
