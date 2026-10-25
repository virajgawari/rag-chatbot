import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, deleteDocument, listDocuments, streamChat } from "./api";
import type { DocumentItem, Message } from "./types";
import { Brand } from "./components/Brand";
import { UploadScreen } from "./components/UploadScreen";
import { Sidebar } from "./components/Sidebar";
import { ChatHeader } from "./components/ChatHeader";
import { MessageList } from "./components/MessageList";
import { Composer } from "./components/Composer";

const prompts = [
  "What are the key takeaways?",
  "Summarize this document",
  "What should I know first?",
];

export default function App() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [activeId, setActiveId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState("");

  const active = documents.find((document) => document.id === activeId);
  const abortRef = useRef<AbortController | null>(null);
  const initialized = useRef(false);
  const lastActiveId = useRef("");

  const cancelGeneration = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  const refreshDocuments = useCallback(async (signal?: AbortSignal) => {
    const items = await listDocuments(signal);
    setDocuments(items);

    // Select the first ready document only on the initial load. Keep the ref
    // mutation out of a state updater: React may invoke updaters more than
    // once (StrictMode/concurrent rendering), which could otherwise clear the
    // selection on reload.
    if (!initialized.current) {
      initialized.current = true;
      setActiveId(items.find((item) => item.status === "ready")?.id ?? "");
      return;
    }

    setActiveId((current) =>
      current && items.some((item) => item.id === current) ? current : "",
    );
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refreshDocuments(controller.signal)
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setNotice("Could not load your documents. Is the backend running?");
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [refreshDocuments]);

  const hasPending = documents.some(
    (document) => document.status !== "ready" && document.status !== "failed",
  );

  useEffect(() => {
    if (!hasPending) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      refreshDocuments(controller.signal).catch(() => {
        // Keep the last known state; the next poll can recover.
      });
    }, 3000);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [hasPending, refreshDocuments]);

  const addDocument = (document: DocumentItem) => {
    initialized.current = true;
    setNotice("");
    setDeleteError("");
    setDocuments((items) => [document, ...items.filter((item) => item.id !== document.id)]);
    lastActiveId.current = document.id;
    setActiveId(document.id);
    setMessages([]);
  };

  const runQuestion = async (question: string, addUserMessage: boolean) => {
    if (!active || active.status !== "ready" || busy || !question.trim()) return;

    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    const assistantId = crypto.randomUUID();
    setBusy(true);
    setMessages((items) => [
      ...items,
      ...(addUserMessage
        ? [{ id: crypto.randomUUID(), role: "user" as const, content: question }]
        : []),
      { id: assistantId, role: "assistant", content: "", retrieving: true },
    ]);

    try {
      for await (const event of streamChat({
        documentId: active.id,
        message: question,
        signal: controller.signal,
      })) {
        if (controller.signal.aborted) break;
        if (event.type === "token") {
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? {
                    ...item,
                    retrieving: false,
                    streaming: true,
                    content: item.content + event.value,
                  }
                : item,
            ),
          );
        } else if (event.type === "sources") {
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? { ...item, retrieving: false, streaming: false, sources: event.value }
                : item,
            ),
          );
        } else if (event.type === "error") {
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? {
                    ...item,
                    retrieving: false,
                    streaming: false,
                    error: true,
                    errorMessage: event.message,
                    retry: question,
                  }
                : item,
            ),
          );
        } else if (event.type === "done") {
          setMessages((items) =>
            items.map((item) =>
              item.id === assistantId
                ? { ...item, retrieving: false, streaming: false }
                : item,
            ),
          );
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        const message =
          error instanceof ApiError
            ? error.message
            : "The answer could not be generated. Please retry.";
        setMessages((items) =>
          items.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  retrieving: false,
                  streaming: false,
                  error: true,
                  errorMessage: message,
                  retry: question,
                }
              : item,
          ),
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setBusy(false);
      }
      setMessages((items) =>
        items
          .map((item) =>
            item.id === assistantId
              ? { ...item, retrieving: false, streaming: false }
              : item,
          )
          .filter(
            (item) =>
              item.id !== assistantId ||
              item.error ||
              item.content !== "" ||
              Boolean(item.sources?.length),
          ),
      );
    }
  };

  const retry = (question: string) => {
    if (busy) return;
    setMessages((items) => items.filter((item) => !item.error));
    void runQuestion(question, false);
  };

  const chooseDocument = (id: string) => {
    cancelGeneration();
    setActiveId(id);
    setMessages([]);
    setDeleteError("");
    setDrawerOpen(false);
  };

  const removeDocument = async (id: string) => {
    if (deletingId) return;
    const target = documents.find((document) => document.id === id);
    if (!target) return;
    if (
      !window.confirm(
        `Delete "${target.name}"? The file and its indexed chunks will be removed.`,
      )
    ) {
      return;
    }

    const finishLocalDelete = () => {
      const remaining = documents.filter((document) => document.id !== id);
      setDocuments(remaining);
      if (id !== activeId) return;

      cancelGeneration();
      setMessages([]);
      const next =
        remaining.find((document) => document.status === "ready") ??
        remaining.find((document) => document.status !== "failed") ??
        remaining[0];
      setActiveId(next?.id ?? "");
      lastActiveId.current = next?.id ?? "";
      setNotice(next ? "" : "Document deleted. Upload a new document to continue.");
    };

    setDeletingId(id);
    setDeleteError("");
    try {
      await deleteDocument(id);
      finishLocalDelete();
    } catch (error) {
      // A 404 means the document is already gone; converge on that state.
      if (error instanceof ApiError && error.status === 404) {
        finishLocalDelete();
      } else {
        setDeleteError(
          error instanceof ApiError
            ? error.message
            : "The document could not be deleted. Please retry.",
        );
      }
    } finally {
      setDeletingId(null);
    }
  };

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-white">
        <Brand />
      </div>
    );
  }

  if (!active) {
    return (
      <UploadScreen
        onReady={addDocument}
        onBack={
          documents.length
            ? () => {
                const target =
                  lastActiveId.current ||
                  documents.find((document) => document.status === "ready")?.id ||
                  "";
                if (target) chooseDocument(target);
              }
            : undefined
        }
        notice={notice}
      />
    );
  }

  const ready = active.status === "ready";
  const failed = active.status === "failed";

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        documents={documents}
        activeId={activeId}
        onNew={() => {
          cancelGeneration();
          lastActiveId.current = activeId;
          setActiveId("");
          setMessages([]);
          setDeleteError("");
        }}
        onSelect={chooseDocument}
        onDelete={(id) => void removeDocument(id)}
        deletingId={deletingId}
        error={deleteError}
        onDismissError={() => setDeleteError("")}
        open={drawerOpen}
        onOpen={() => setDrawerOpen(true)}
        onClose={() => setDrawerOpen(false)}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <ChatHeader
          name={active.name}
          status={active.status}
          onClear={() => {
            cancelGeneration();
            setMessages([]);
          }}
        />
        {messages.length ? (
          <MessageList messages={messages} onRetry={retry} />
        ) : (
          <div className="flex flex-1 flex-col justify-end overflow-y-auto">
            <div className="mx-auto w-full max-w-[720px] px-5 pb-7 md:px-8">
              <p className="mb-5 text-sm text-muted">
                {ready
                  ? "Try asking:"
                  : failed
                    ? "This document could not be indexed. Upload it again to continue."
                    : "This document is still being indexed."}
              </p>
              {ready && (
                <div className="flex flex-wrap gap-2">
                  {prompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => void runQuestion(prompt, true)}
                      className="rounded-full border border-control bg-white px-3 py-2 text-[13px] font-medium hover:bg-[#F9FAFB]"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
        <Composer
          busy={busy}
          disabled={!ready}
          disabledHint={
            failed
              ? "This document could not be indexed."
              : "This document is still being indexed."
          }
          onSend={(text) => void runQuestion(text, true)}
          onStop={cancelGeneration}
        />
      </main>
    </div>
  );
}
