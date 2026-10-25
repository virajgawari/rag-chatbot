import { useEffect, useRef, useState } from "react";
import { DropZone } from "./DropZone";
import { FileProgressRow } from "./FileProgressRow";
import { Brand } from "./Brand";
import { ApiError, getDocumentStatus, uploadDocument } from "../api";
import type { DocumentItem } from "../types";

type UploadError = { message: string; retry?: () => void };
type Props = {
  onReady: (document: DocumentItem) => void;
  onBack?: () => void;
  notice?: string;
};

const INDEXING_TIMEOUT_MS = 10 * 60 * 1000;

export function UploadScreen({ onReady, onBack, notice }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [stageLabel, setStageLabel] = useState("Uploading document…");
  const [error, setError] = useState<UploadError | null>(null);

  const runId = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const begin = async (picked: File) => {
    const run = runId.current + 1;
    runId.current = run;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setError(null);
    setFile(picked);
    setProgress(4);
    setStageLabel("Uploading document…");

    try {
      const response = await uploadDocument(picked, controller.signal);
      if (run !== runId.current) return;

      setStageLabel("Indexing document…");
      setProgress(8);
      const startedAt = Date.now();

      while (!controller.signal.aborted && run === runId.current) {
        if (Date.now() - startedAt > INDEXING_TIMEOUT_MS) {
          throw new ApiError(
            "Indexing is taking longer than expected. Please try again.",
            0,
            "indexing_timeout",
          );
        }

        const status = await getDocumentStatus(response.documentId, controller.signal);
        if (run !== runId.current) return;
        setProgress(status.progressPercentage);

        if (status.status === "ready") {
          setProgress(100);
          window.setTimeout(() => {
            if (run === runId.current) {
              onReady({
                id: status.documentId,
                name: status.name,
                status: "ready",
                progressPercentage: 100,
                totalChunks: status.totalChunks,
              });
            }
          }, 300);
          return;
        }

        if (status.status === "failed") {
          throw new ApiError(
            status.errorMessage ?? "The document could not be indexed. Please try again.",
            0,
            "document_failed",
          );
        }

        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
    } catch (caught) {
      if (controller.signal.aborted || run !== runId.current) return;
      setFile(null);
      setProgress(0);
      setStageLabel("Uploading document…");
      setError({
        message:
          caught instanceof ApiError
            ? caught.message
            : "The upload failed. Check the backend connection and try again.",
        retry: () => void begin(picked),
      });
    }
  };

  const cancel = () => {
    runId.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setFile(null);
    setProgress(0);
  };

  return (
    <div className="min-h-screen bg-white">
      <header className="mx-auto flex h-16 max-w-[1200px] items-center justify-between px-6">
        <Brand />
        <div className="flex items-center gap-4">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="text-[13px] font-medium text-muted hover:text-ink"
            >
              Back to chat
            </button>
          )}
          <a href="#help" className="text-[13px] font-medium text-muted hover:text-ink">
            Help
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-[640px] px-6 pb-16 pt-20 text-center">
        <h1 className="m-0 text-[32px] font-semibold tracking-[-0.02em]">
          Chat with your document
        </h1>
        <p className="mx-auto mb-9 mt-3 max-w-[560px] leading-6 text-muted">
          Upload a file, then ask questions in plain language. Every answer points back to the
          pages it came from.
        </p>

        {notice && !file && (
          <p role="status" className="mb-4 text-left text-sm text-muted">
            {notice}
          </p>
        )}

        <DropZone
          onFile={begin}
          onError={(message, retry) => setError({ message, retry })}
        />

        {file && (
          <FileProgressRow
            name={file.name}
            progress={progress}
            status={stageLabel}
            onCancel={cancel}
          />
        )}

        {error && (
          <p role="alert" className="mt-4 text-left text-sm text-[#B42318]">
            {error.message}{" "}
            {error.retry && (
              <button
                type="button"
                onClick={() => error.retry?.()}
                className="font-medium underline"
              >
                Retry
              </button>
            )}{" "}
            <button
              type="button"
              onClick={() => setError(null)}
              className="font-medium underline"
            >
              Dismiss
            </button>
          </p>
        )}
      </main>
    </div>
  );
}
