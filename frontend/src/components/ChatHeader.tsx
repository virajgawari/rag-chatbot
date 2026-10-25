import { Trash2 } from "lucide-react";
import type { DocumentStatus } from "../types";

const STATUS = {
  ready: {
    label: "Indexed",
    className: "bg-[#F0FDF4] text-[#166534]",
    dot: "bg-[#22C55E]",
  },
  failed: {
    label: "Indexing failed",
    className: "bg-[#FEF2F2] text-[#B42318]",
    dot: "bg-[#EF4444]",
  },
  indexing: {
    label: "Indexing",
    className: "bg-[#FFFBEB] text-[#92400E]",
    dot: "bg-[#F59E0B]",
  },
} as const;

function statusStyle(status: DocumentStatus) {
  if (status === "ready") return STATUS.ready;
  if (status === "failed") return STATUS.failed;
  return STATUS.indexing;
}

export function ChatHeader({
  name,
  status,
  onClear,
}: {
  name: string;
  status: DocumentStatus;
  onClear: () => void;
}) {
  const state = statusStyle(status);
  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b px-6 md:px-8">
      <div className="ml-10 flex min-w-0 items-center gap-2.5 md:ml-0">
        <span className="truncate text-[15px] font-semibold">{name}</span>
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${state.className}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${state.dot}`} />
          {state.label}
        </span>
      </div>
      <button
        type="button"
        onClick={onClear}
        className="inline-flex h-9 shrink-0 items-center gap-2 rounded-[10px] border border-control bg-white px-3 text-[13px] font-medium hover:bg-[#F9FAFB]"
      >
        <Trash2 size={15} aria-hidden="true" />
        Clear chat
      </button>
    </header>
  );
}
