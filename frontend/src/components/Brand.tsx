import { FileText } from "lucide-react";

export function Brand() {
  return <div className="flex items-center gap-2.5 font-semibold tracking-[-0.02em]"><span className="grid h-7 w-7 place-items-center rounded-[8px] bg-accent text-white"><FileText size={16} aria-hidden="true" /></span><span>DocChat</span></div>;
}
