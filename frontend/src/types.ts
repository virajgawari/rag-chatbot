export type DocumentStatus =
  | "queued"
  | "parsing"
  | "chunking"
  | "embedding"
  | "ready"
  | "failed";

export type DocumentItem = {
  id: string;
  name: string;
  status: DocumentStatus;
  progressPercentage?: number;
  totalChunks?: number;
  errorMessage?: string | null;
};

export type Source = {
  filename: string;
  page: number;
  snippet: string;
  chunkIndex: number;
  score: number;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  /** true until the first token arrives */
  retrieving?: boolean;
  /** true while tokens are streaming in */
  streaming?: boolean;
  /** true when generation failed; content is empty and retry holds the question */
  error?: boolean;
  errorMessage?: string;
  retry?: string;
};

export type UploadResponse = {
  documentId: string;
  name: string;
  status: DocumentStatus;
  sizeBytes: number;
};

export type DocumentStatusResponse = {
  documentId: string;
  name: string;
  status: DocumentStatus;
  progressPercentage: number;
  totalPages: number;
  totalChunks: number;
  chunksProcessed: number;
  errorMessage: string | null;
};

export type ChatStreamEvent =
  | { type: "token"; value: string }
  | { type: "sources"; value: Source[] }
  | { type: "done" }
  | { type: "error"; message: string; code?: string };
