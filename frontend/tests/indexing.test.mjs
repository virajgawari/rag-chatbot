import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Exercise the actual TS helper without adding a browser/test-runner dependency.
const source = readFileSync(new URL("../src/indexing.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
});
const { formatDuration, indexingProgress } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

test("formats elapsed and remaining time without negative or NaN values", () => {
  assert.equal(formatDuration(12.1), "13s");
  assert.equal(formatDuration(125), "2m 5s");
  assert.equal(formatDuration(3700), "1h 1m");
  assert.equal(formatDuration(-10), "0s");
  assert.equal(formatDuration(NaN), "0s");
});

test("shows actual chunk counts and labels ETA as an estimate", () => {
  const result = indexingProgress({
    status: "embedding", totalChunks: 716, chunksProcessed: 120,
    elapsedSeconds: 90, estimatedRemainingSeconds: 250,
  });
  assert.equal(result.label, "Indexing passages…");
  assert.match(result.details, /120 of 716 chunks/);
  assert.match(result.details, /1m 30s elapsed/);
  assert.match(result.details, /~4m 10s remaining \(estimate\)/);
});

test("does not invent ETA before measurements or with an older backend", () => {
  const queued = indexingProgress({ status: "queued" });
  assert.equal(queued.label, "Waiting to index…");
  assert.equal(queued.details, "");
  const embedding = indexingProgress({ status: "embedding", totalChunks: 716 });
  assert.equal(embedding.details, "0 of 716 chunks");
  assert.equal(indexingProgress({ status: "parsing" }).label, "Reading document…");
});

test("long indexing no longer throws a ten-minute timeout and triggers duplicate uploads", () => {
  const upload = readFileSync(new URL("../src/components/UploadScreen.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(upload, /"indexing_timeout"/);
  assert.match(upload, /You do not need to upload it again/);
});
