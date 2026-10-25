import { FileText, X } from "lucide-react";

export function FileProgressRow({
  name,
  progress,
  status = "Indexing document…",
  onCancel,
}: {
  name: string;
  progress: number;
  status?: string;
  onCancel: () => void;
}) {
  return (
    <div className="mt-4 flex items-center gap-3 rounded-2xl border bg-white p-3.5">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[10px] bg-tint text-accent">
        <FileText size={19} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex justify-between gap-3">
          <span className="truncate font-medium">{name}</span>
          <span className="shrink-0 text-[13px] text-muted" aria-live="polite">
            {status}
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="Document indexing progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          className="mt-2 h-1 overflow-hidden rounded-full bg-[#E5E7EB]"
        >
          <div
            className="h-full rounded-full bg-accent transition-all"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
      <button
        type="button"
        aria-label="Cancel upload"
        onClick={onCancel}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted hover:bg-[#F3F4F6]"
      >
        <X size={18} aria-hidden="true" />
      </button>
    </div>
  );
}
