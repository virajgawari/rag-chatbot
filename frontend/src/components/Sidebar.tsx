import { FilePlus2, FileText, Loader2, Menu, Trash2, X } from "lucide-react";
import { Brand } from "./Brand";
import type { DocumentItem } from "../types";

type Props = {
  documents: DocumentItem[];
  activeId: string;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  deletingId?: string | null;
  error?: string;
  onDismissError?: () => void;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
};

export function Sidebar({
  documents,
  activeId,
  onNew,
  onSelect,
  onDelete,
  deletingId,
  error,
  onDismissError,
  open,
  onOpen,
  onClose,
}: Props) {
  return (
    <>
      <button
        type="button"
        aria-label="Open documents"
        onClick={onOpen}
        className="fixed left-4 top-4 z-20 grid h-9 w-9 place-items-center rounded-lg border bg-white md:hidden"
      >
        <Menu size={18} aria-hidden="true" />
      </button>
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-[264px] flex-col border-r bg-sidebar p-4 transition-transform md:static md:translate-x-0 ${
          open ? "visible translate-x-0" : "invisible -translate-x-full md:visible"
        }`}
      >
        <div className="flex h-8 items-center justify-between px-1">
          <Brand />
          <button
            type="button"
            aria-label="Close documents"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-lg md:hidden"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <button
          type="button"
          onClick={onNew}
          className="mt-7 flex h-10 w-full items-center justify-center gap-2 rounded-[10px] border border-control bg-white text-[13px] font-medium hover:bg-[#F9FAFB]"
        >
          <FilePlus2 size={16} aria-hidden="true" />
          New document
        </button>
        <p className="mb-2 mt-7 px-1 text-xs font-semibold tracking-[0.08em] text-muted">
          DOCUMENTS
        </p>
        <nav aria-label="Uploaded documents" className="space-y-1">
          {documents.map((document) => {
            const isActive = document.id === activeId;
            const isDeleting = deletingId === document.id;
            return (
              <div
                key={document.id}
                className={`flex items-center rounded-[10px] border ${
                  isActive
                    ? "border-control bg-white"
                    : "border-transparent hover:bg-white/70"
                }`}
              >
                <button
                  type="button"
                  onClick={() => {
                    onSelect(document.id);
                    onClose();
                  }}
                  aria-current={isActive ? "true" : undefined}
                  className={`flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left text-[13px] ${
                    isActive ? "font-medium" : ""
                  }`}
                >
                  <FileText size={16} className="shrink-0 text-muted" aria-hidden="true" />
                  <span className="truncate">{document.name}</span>
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(document.id)}
                  disabled={Boolean(deletingId)}
                  aria-label={`Delete ${document.name}`}
                  title={`Delete ${document.name}`}
                  className="mr-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted hover:bg-[#FEF3F2] hover:text-[#B42318] disabled:opacity-50"
                >
                  {isDeleting ? (
                    <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Trash2 size={15} aria-hidden="true" />
                  )}
                </button>
              </div>
            );
          })}
        </nav>
        {error && (
          <div
            role="alert"
            className="mt-4 rounded-lg border border-[#FDA29B] bg-[#FEF3F2] px-3 py-2 text-[12px] leading-5 text-[#B42318]"
          >
            <span className="block">{error}</span>
            {onDismissError && (
              <button
                type="button"
                onClick={onDismissError}
                className="mt-1 font-medium underline"
              >
                Dismiss
              </button>
            )}
          </div>
        )}
      </aside>
      {open && (
        <button
          aria-label="Close document drawer"
          onClick={onClose}
          className="fixed inset-0 z-20 bg-black/20 md:hidden"
        />
      )}
    </>
  );
}
