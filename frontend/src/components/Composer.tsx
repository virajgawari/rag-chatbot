import { ArrowUp, Square } from "lucide-react";
import { FormEvent, KeyboardEvent, useRef, useState } from "react";

export function Composer({
  onSend,
  onStop,
  busy,
  disabled = false,
  disabledHint = "This document is still being indexed.",
}: {
  onSend: (message: string) => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
  disabledHint?: string;
}) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!busy && !disabled && text.trim()) {
      onSend(text.trim());
      setText("");
    }
  };

  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="shrink-0 bg-white px-5 pb-5 pt-3 md:px-8">
      <form onSubmit={submit} className="mx-auto max-w-[720px]">
        <div className="flex items-end gap-2 rounded-2xl border border-control bg-white p-2 shadow-composer">
          <textarea
            ref={area}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={keyDown}
            aria-label="Ask a question about this document"
            placeholder={
              disabled ? disabledHint : "Ask a question about this document…"
            }
            rows={1}
            disabled={disabled}
            className="max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[15px] leading-6 placeholder:text-muted disabled:cursor-not-allowed disabled:opacity-60"
          />
          {busy ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generating"
              className="grid h-10 w-10 place-items-center rounded-[10px] bg-accent text-white hover:bg-[#4338CA]"
            >
              <Square size={15} fill="currentColor" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={disabled || !text.trim()}
              aria-label="Send message"
              className="grid h-10 w-10 place-items-center rounded-[10px] bg-accent text-white enabled:hover:bg-[#4338CA] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ArrowUp size={19} />
            </button>
          )}
        </div>
        <p className="mb-0 mt-2 text-center text-xs text-muted">
          {disabled ? disabledHint : "Answers are based only on the uploaded document."}
        </p>
      </form>
    </div>
  );
}
