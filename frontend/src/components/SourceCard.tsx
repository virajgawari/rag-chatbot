import type { Source } from "../types";

export function SourceCard({ source }: { source: Source }) {
  return (
    <div className="mt-3 rounded-xl border bg-[#F9FAFB] p-3.5 text-[13px] leading-5 text-muted">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="inline-block rounded-full bg-white px-2 py-0.5 text-xs font-medium text-ink">
          Page {source.page}
        </span>
        <span className="truncate text-xs" title={source.filename}>
          {source.filename}
        </span>
      </div>
      <p className="m-0 whitespace-pre-line">{source.snippet}</p>
    </div>
  );
}
