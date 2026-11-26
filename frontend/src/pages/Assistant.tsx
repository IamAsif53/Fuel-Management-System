import { useState } from "react";
import {
  Bot,
  Send,
  Sparkles,
  FileText,
  AlertCircle,
  ExternalLink,
} from "lucide-react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import { askAssistant, fetchSummary } from "@/api/client";
import type { AssistantResponse, SummaryResponse } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { formatNumber } from "@/lib/format";

interface ChatMessage {
  id: string;
  sender: "user" | "assistant";
  text: string;
  engine?: "llm" | "rules";
  sources?: Array<{ type: string; id: string }>;
  timestamp: string;
}

const SUGGESTED_QUESTIONS = [
  "Why is Cox's Bazar at risk?",
  "Show me active shortage alerts",
  "What is the latest dispatch recommendation?",
  "How is system health right now?",
  "Summarize the current shift",
];

export default function Assistant() {
  const { state } = useLiveState();
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      sender: "assistant",
      text: "Hello! I am the FSIP Operations Assistant. I answer control-room questions strictly grounded in current platform state, active risks, and real-time alerts.",
      engine: "rules",
      sources: [],
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    },
  ]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Shift summary modal / drawer
  const [summaryData, setSummaryData] = useState<SummaryResponse | null>(null);
  const [isSummaryLoading, setIsSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const handleSend = async (qToSend?: string) => {
    const textToSend = (qToSend || question).trim();
    if (!textToSend || isLoading) return;

    setError(null);
    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: "user",
      text: textToSend,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setQuestion("");
    setIsLoading(true);

    try {
      const res: AssistantResponse = await askAssistant(textToSend);
      const assistantMsg: ChatMessage = {
        id: `asst-${Date.now()}`,
        sender: "assistant",
        text: res.answer,
        engine: res.engine,
        sources: res.sources,
        timestamp: new Date(res.generated_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to obtain response from assistant";
      setError(msg);
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          sender: "assistant",
          text: `Error: ${msg}. Please check platform network connectivity and retry.`,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleGenerateSummary = async () => {
    setIsSummaryLoading(true);
    setSummaryError(null);
    try {
      const res = await fetchSummary(96);
      setSummaryData(res);
    } catch (err: unknown) {
      setSummaryError(err instanceof Error ? err.message : "Failed to generate shift summary");
    } finally {
      setIsSummaryLoading(false);
    }
  };

  const getSourceLink = (type: string, id: string) => {
    switch (type) {
      case "station":
        return `/network?station=${id}`;
      case "depot":
        return `/network?depot=${id}`;
      case "alert":
        return `/alerts`;
      case "decision":
        return id ? `/decisions/${id}` : `/decisions`;
      default:
        return null;
    }
  };

  return (
    <div className="flex min-h-0 max-h-[calc(100vh-5.5rem)] flex-1 flex-col space-y-4 overflow-hidden">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-bg-border pb-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-ink">
            <Bot className="text-accent" size={24} />
            Operations Assistant
          </h1>
          <p className="mt-1 text-xs text-ink-muted">
            Grounded LLM & rules assistant answering control-room questions from platform telemetry.
          </p>
        </div>

        <button
          onClick={handleGenerateSummary}
          disabled={isSummaryLoading}
          className="flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-3.5 py-1.5 text-xs font-semibold text-accent hover:bg-accent/20 transition-colors shadow-sm"
        >
          <FileText size={14} className={clsx(isSummaryLoading && "animate-spin")} />
          Generate Shift Summary
        </button>
      </div>

      {/* Degraded State Notice if active */}
      {state?.degraded && (
        <div className="flex items-center gap-2 rounded-lg border border-risk-high/40 bg-risk-high/10 p-3 text-xs text-risk-high">
          <AlertCircle size={16} />
          <span>
            Operating in Degraded State. Assistant responses are constrained strictly to last-good verified telemetry.
          </span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-xs text-red-400">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {summaryError && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-xs text-red-400">
          <AlertCircle size={16} />
          <span>{summaryError}</span>
        </div>
      )}

      {/* Shift Summary Banner Modal/Drawer */}
      {summaryData && (
        <div className="rounded-xl border border-accent/30 bg-bg-panel p-4 shadow-md text-xs space-y-3">
          <div className="flex items-center justify-between border-b border-bg-border/60 pb-2">
            <span className="font-bold text-ink flex items-center gap-2 text-sm">
              <FileText size={16} className="text-accent" />
              Executive Shift Summary (Window: {summaryData.stats.window_ticks} ticks)
            </span>
            <button
              onClick={() => setSummaryData(null)}
              className="text-ink-muted hover:text-ink font-bold"
            >
              ✕
            </button>
          </div>

          <div className="rounded-lg bg-bg-raised/70 p-3 text-ink leading-relaxed whitespace-pre-line font-mono text-[11px]">
            {summaryData.text}
          </div>

          {/* KPI grid */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded border border-bg-border p-2">
              <span className="text-[10px] text-ink-muted">Service Level</span>
              <div className="font-mono font-bold text-risk-low text-sm">
                {(summaryData.stats.service_level * 100).toFixed(2)}%
              </div>
            </div>
            <div className="rounded border border-bg-border p-2">
              <span className="text-[10px] text-ink-muted">Unmet Demand</span>
              <div className="font-mono font-bold text-risk-high text-sm">
                {formatNumber(summaryData.stats.unmet_liters)} L
              </div>
            </div>
            <div className="rounded border border-bg-border p-2">
              <span className="text-[10px] text-ink-muted">Total Dispatched</span>
              <div className="font-mono font-bold text-ink text-sm">
                {formatNumber(summaryData.stats.total_dispatched_liters)} L
              </div>
            </div>
            <div className="rounded border border-bg-border p-2">
              <span className="text-[10px] text-ink-muted">Decisions</span>
              <div className="font-mono font-bold text-ink text-sm">
                {summaryData.stats.decisions_count} ({summaryData.stats.autopilot_count} auto)
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Chat Messages Viewport */}
      <div className="flex-1 overflow-y-auto space-y-3 p-2 rounded-xl border border-bg-border/70 bg-bg-base/40">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={clsx(
              "flex flex-col max-w-2xl text-xs space-y-1.5",
              msg.sender === "user" ? "ml-auto items-end" : "mr-auto items-start"
            )}
          >
            {/* Header info */}
            <div className="flex items-center gap-2 text-[10px] text-ink-muted px-1">
              <span>{msg.sender === "user" ? "Operator" : "Ops Assistant"}</span>
              <span>•</span>
              <span>{msg.timestamp}</span>
              {msg.engine && (
                <span
                  className={clsx(
                    "rounded px-1.5 py-0.2 font-mono text-[9px] font-bold uppercase",
                    msg.engine === "llm"
                      ? "bg-accent/20 text-accent border border-accent/40"
                      : "bg-bg-raised text-ink-muted border border-bg-border"
                  )}
                >
                  {msg.engine}
                </span>
              )}
            </div>

            {/* Bubble */}
            <div
              className={clsx(
                "rounded-xl px-4 py-3 leading-relaxed shadow-sm",
                msg.sender === "user"
                  ? "bg-brand text-white rounded-tr-none"
                  : "bg-bg-panel border border-bg-border text-ink rounded-tl-none"
              )}
            >
              <div className="whitespace-pre-wrap">{msg.text}</div>

              {/* Source chips */}
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2.5 pt-2 border-t border-bg-border/40 flex flex-wrap items-center gap-1.5">
                  <span className="text-[10px] font-semibold text-ink-muted">Grounded Sources:</span>
                  {msg.sources.map((s, idx) => {
                    const link = getSourceLink(s.type, s.id);
                    return link ? (
                      <Link
                        key={idx}
                        to={link}
                        className="flex items-center gap-1 rounded bg-bg-raised hover:bg-bg-border px-2 py-0.5 font-mono text-[10px] text-accent font-medium transition-colors"
                      >
                        <span>{s.type}:{s.id}</span>
                        <ExternalLink size={10} />
                      </Link>
                    ) : (
                      <span
                        key={idx}
                        className="rounded bg-bg-raised px-2 py-0.5 font-mono text-[10px] text-ink-muted"
                      >
                        {s.type}:{s.id}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="mr-auto max-w-sm rounded-xl border border-bg-border bg-bg-panel p-3 shadow-sm flex items-center gap-2 text-xs text-ink-muted">
            <Bot size={16} className="animate-pulse text-accent" />
            <span>Analyzing current platform state and signals...</span>
          </div>
        )}
      </div>

      {/* Suggested prompts pills */}
      <div className="flex flex-wrap items-center gap-1.5 px-1">
        <span className="text-[11px] font-semibold text-ink-muted flex items-center gap-1 mr-1">
          <Sparkles size={12} className="text-accent" />
          Suggested:
        </span>
        {SUGGESTED_QUESTIONS.map((q, idx) => (
          <button
            key={idx}
            onClick={() => handleSend(q)}
            disabled={isLoading}
            className="rounded-full border border-bg-border bg-bg-panel hover:bg-bg-raised px-2.5 py-1 text-[11px] text-ink-muted hover:text-ink transition-colors shadow-2xs"
          >
            {q}
          </button>
        ))}
      </div>

      {/* Input row */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
        className="flex items-center gap-2"
      >
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about station inventory, shortage risk, decisions, alerts, or system health..."
          disabled={isLoading}
          maxLength={500}
          className="flex-1 rounded-xl border border-bg-border bg-bg-panel px-4 py-2.5 text-xs text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand shadow-sm"
        />
        <button
          type="submit"
          disabled={isLoading || !question.trim()}
          className="flex items-center gap-1.5 rounded-xl bg-brand px-4 py-2.5 text-xs font-semibold text-white hover:bg-brand/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
        >
          <Send size={14} />
          Ask
        </button>
      </form>
    </div>
  );
}
