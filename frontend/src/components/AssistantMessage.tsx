import { AlertCircle, FileText, RotateCcw } from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { Message } from "../types";
import { SourceChips } from "./SourceChips";

function RetrievingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1" role="status" aria-label="Retrieving answer">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className="dot h-1.5 w-1.5 rounded-full bg-[#9CA3AF]"
          style={{ animationDelay: `${index * 0.15}s` }}
        />
      ))}
    </div>
  );
}

export function AssistantMessage({
  message,
  onRetry,
}: {
  message: Message;
  onRetry?: (question: string) => void;
}) {
  return (
    <div className="flex max-w-full gap-3">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-accent text-white">
        <FileText size={15} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        {message.error ? (
          <p
            role="alert"
            className="m-0 flex flex-wrap items-center gap-2 text-[15px] text-[#B42318]"
          >
            <AlertCircle size={16} className="shrink-0" aria-hidden="true" />
            <span>{message.errorMessage ?? "The answer could not be generated."}</span>
            {message.retry && onRetry && (
              <button
                type="button"
                onClick={() => onRetry(message.retry!)}
                className="inline-flex items-center gap-1 font-medium text-accent underline"
              >
                <RotateCcw size={14} aria-hidden="true" />
                Retry
              </button>
            )}
          </p>
        ) : (
          <>
            <div
              className={`markdown max-w-[640px] leading-[1.65] ${
                message.streaming ? "blink-cursor" : ""
              }`}
            >
              {message.content ? (
                <ReactMarkdown>{message.content}</ReactMarkdown>
              ) : message.retrieving ? (
                <RetrievingDots />
              ) : null}
            </div>
            {message.sources && <SourceChips sources={message.sources} />}
          </>
        )}
      </div>
    </div>
  );
}
