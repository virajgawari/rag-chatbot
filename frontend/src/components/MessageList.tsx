import { useEffect, useRef } from "react";
import type { Message } from "../types";
import { UserMessage } from "./UserMessage";
import { AssistantMessage } from "./AssistantMessage";

export function MessageList({
  messages,
  onRetry,
}: {
  messages: Message[];
  onRetry?: (question: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-[720px] flex-col gap-7 px-5 py-9 md:px-8">
        {messages.map((message) =>
          message.role === "user" ? (
            <UserMessage key={message.id} content={message.content} />
          ) : (
            <AssistantMessage key={message.id} message={message} onRetry={onRetry} />
          ),
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
