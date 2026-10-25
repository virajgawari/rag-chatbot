import { ChangeEvent, DragEvent, useRef, useState } from "react";
import { Upload } from "lucide-react";

const accepted = ["application/pdf", "text/plain", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"];

type ErrorFn = (message: string, retry?: () => void) => void;
export function DropZone({ onFile, onError }: { onFile: (file: File) => void; onError: ErrorFn }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const openPicker = () => inputRef.current?.click();
  const receive = (file?: File) => {
    if (!file) return;
    if (!accepted.includes(file.type) && !/\.(pdf|docx|txt)$/i.test(file.name)) return onError("Unsupported file type. Please choose a PDF, DOCX, or TXT file.", openPicker);
    if (file.size > 20 * 1024 * 1024) return onError("That file is larger than the 20 MB upload limit.", openPicker);
    onFile(file);
  };
  const drop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); receive(event.dataTransfer.files[0]); };
  return <div onDrop={drop} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} className={`rounded-2xl border-[1.5px] border-dashed bg-[#F9FAFB] p-12 text-center ${dragging ? "border-accent bg-tint" : "border-[#C4CAD4]"}`}>
    <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-full border bg-white text-accent"><Upload size={20} aria-hidden="true" /></div>
    <p className="m-0 font-medium">Drag and drop your document here</p><p className="my-2 text-sm text-muted">or</p>
    <button type="button" onClick={openPicker} className="h-11 rounded-[10px] bg-accent px-5 font-medium text-white hover:bg-[#4338CA]">Browse files</button>
    <p className="mb-0 mt-3 text-[13px] text-muted">PDF, DOCX or TXT</p>
    <input ref={inputRef} className="sr-only" type="file" accept=".pdf,.docx,.txt" tabIndex={-1} aria-hidden="true" onChange={(event: ChangeEvent<HTMLInputElement>) => { const picked = event.target.files?.[0]; event.target.value = ""; receive(picked); }} />
  </div>;
}
