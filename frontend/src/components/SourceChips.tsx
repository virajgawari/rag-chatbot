import { useState } from "react";
import type { Source } from "../types";
import { SourceCard } from "./SourceCard";

type CitationGroup = {
  key: string;
  filename: string;
  page: number;
  snippets: string[];
  score: number;
};

function groupByPage(sources: Source[]): CitationGroup[] {
  const groups = new Map<string, CitationGroup>();
  for (const source of sources) {
    const key = `${source.filename}:${source.page}`;
    const existing = groups.get(key);
    if (existing) {
      if (!existing.snippets.includes(source.snippet)) existing.snippets.push(source.snippet);
      existing.score = Math.max(existing.score, source.score);
    } else {
      groups.set(key, {
        key,
        filename: source.filename,
        page: source.page,
        snippets: [source.snippet],
        score: source.score,
      });
    }
  }
  return Array.from(groups.values());
}

export function SourceChips({ sources }: { sources: Source[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const groups = groupByPage(sources);

  if (!groups.length) return null;

  return (
    <div className="mt-4">
      <p className="mb-2 text-[11px] font-semibold tracking-[0.08em] text-muted">SOURCES</p>
      <div className="flex flex-wrap gap-2">
        {groups.map((group) => (
          <button
            type="button"
            key={group.key}
            onClick={() => setExpanded(expanded === group.key ? null : group.key)}
            aria-expanded={expanded === group.key}
            title={`${group.filename} — page ${group.page}`}
            className="rounded-full border border-control bg-white px-3 py-1.5 text-[13px] font-medium hover:bg-[#F9FAFB]"
          >
            Page {group.page}
          </button>
        ))}
      </div>
      {groups.map(
        (group) =>
          expanded === group.key && (
            <SourceCard
              key={group.key}
              source={{
                filename: group.filename,
                page: group.page,
                snippet: group.snippets.join("\n\n"),
                chunkIndex: 0,
                score: group.score,
              }}
            />
          ),
      )}
    </div>
  );
}
