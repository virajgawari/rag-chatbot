import type {
  ChatStreamEvent,
  DocumentItem,
  DocumentStatus,
  DocumentStatusResponse,
  Source,
  UploadResponse,
} from "./types";

const configuredBase = (import.meta.env.VITE_API_BASE_URL ?? "").trim();

function normalizeBase(value: string): string {
  if (!value) return "/api/v1";
  const trimmed = value.replace(/\/+$/, "");
  return trimmed.endsWith("/api/v1") ? trimmed : `${trimmed}/api/v1`;
}

export const API_BASE_URL = normalizeBase(configuredBase);

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status = 0, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      credentials: "omit",
      headers,
    });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError(
      "Could not reach the DocChat API. Check that the backend is running.",
      0,
      "network_error",
    );
  }
}

async function responseError(response: Response): Promise<ApiError> {
  let message = `The request failed (HTTP ${response.status}).`;
  let code: string | undefined;
  try {
    const payload = await response.json();
    code = typeof payload.code === "string" ? payload.code : undefined;
    if (typeof payload.detail === "string") {
      message = payload.detail;
    } else if (Array.isArray(payload.detail) && payload.detail[0]?.msg) {
      message = String(payload.detail[0].msg);
    }
  } catch {
    // Keep the generic message when the response has no JSON body.
  }
  return new ApiError(message, response.status, code);
}

export async function listDocuments(signal?: AbortSignal): Promise<DocumentItem[]> {
  const response = await request("/documents?limit=100&offset=0", { signal });
  if (!response.ok) throw await responseError(response);
  const items = (await response.json()) as Array<{
    document_id: string;
    name: string;
    status: DocumentStatus;
    total_chunks: number;
    total_pages: number;
  }>;
  return items.map((item) => ({
    id: item.document_id,
    name: item.name,
    status: item.status,
    totalChunks: item.total_chunks,
  }));
}

export async function deleteDocument(documentId: string, signal?: AbortSignal): Promise<void> {
  const response = await request(`/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
    signal,
  });
  if (!response.ok) throw await responseError(response);
}

export async function uploadDocument(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file);
  const response = await request("/documents/upload", { method: "POST", body, signal });
  if (!response.ok) throw await responseError(response);
  const payload = await response.json();
  return {
    documentId: payload.document_id,
    name: payload.name,
    status: payload.status,
    sizeBytes: payload.size_bytes,
  };
}

export async function getDocumentStatus(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentStatusResponse> {
  const response = await request(`/documents/${encodeURIComponent(documentId)}/status`, { signal });
  if (!response.ok) throw await responseError(response);
  const payload = await response.json();
  return {
    documentId: payload.document_id,
    name: payload.name,
    status: payload.status,
    progressPercentage: payload.progress_percentage,
    totalPages: payload.total_pages,
    totalChunks: payload.total_chunks,
    chunksProcessed: payload.chunks_processed,
    errorMessage: payload.error_message ?? null,
  };
}

type RawSource = {
  filename: string;
  page: number;
  snippet: string;
  chunk_index: number;
  score: number;
};

function toSource(raw: RawSource): Source {
  return {
    filename: raw.filename,
    page: Number(raw.page) || 1,
    snippet: raw.snippet,
    chunkIndex: Number(raw.chunk_index) || 0,
    score: Number(raw.score) || 0,
  };
}

function parseSseBlock(block: string): { event: string; data: unknown } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    if (separator < 0) continue;
    const field = line.slice(0, separator);
    let value = line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}

export async function* streamChat(input: {
  documentId: string;
  message: string;
  signal?: AbortSignal;
}): AsyncGenerator<ChatStreamEvent> {
  const response = await request("/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ document_id: input.documentId, message: input.message }),
    signal: input.signal,
  });

  // Validation and scoping errors arrive as normal JSON before a stream starts.
  if (!response.ok) throw await responseError(response);
  if (!response.body) {
    throw new ApiError("This browser cannot read the answer stream.", 0, "stream_unsupported");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary >= 0) {
        const separator = buffer.match(/\r?\n\r?\n/);
        if (!separator) break;
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + separator[0].length);
        const parsed = parseSseBlock(block);

        if (parsed) {
          if (parsed.event === "token") {
            const payload = parsed.data as { value?: string };
            if (payload.value) yield { type: "token", value: payload.value };
          } else if (parsed.event === "sources") {
            const payload = parsed.data as { value?: RawSource[] };
            yield { type: "sources", value: (payload.value ?? []).map(toSource) };
          } else if (parsed.event === "error") {
            const payload = parsed.data as { message?: string; code?: string };
            yield {
              type: "error",
              message: payload.message ?? "Answer generation failed. Please retry.",
              code: payload.code,
            };
            return;
          } else if (parsed.event === "done") {
            yield { type: "done" };
            return;
          }
        }
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }

    buffer += decoder.decode();
    const parsed = parseSseBlock(buffer);
    if (parsed?.event === "token") {
      const payload = parsed.data as { value?: string };
      if (payload.value) yield { type: "token", value: payload.value };
    }
  } finally {
    reader.releaseLock();
  }
}
