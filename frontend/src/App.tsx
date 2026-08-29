/**
 * 前端应用主组件
 * 负责聊天会话状态、SSE 事件消费和整体页面布局
 */
import {
  Activity,
  BarChart3,
  Eraser,
  History,
  Leaf,
  MessageSquarePlus,
  Server,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { MessageBubble } from "./components/MessageBubble";
import { streamQuery } from "./lib/agentApi";
import { cn, summarizeResult } from "./lib/format";
import type { AgentEvent, ChatMessage, ProgressEvent, StepState, ToolProgressEvent } from "./types/agent";

const examples = [
  "统计 2025 年第一季度各大区的 GMV，并按 GMV 从高到低排序",
  "统计 2025 年 3 月各商品品类的销量和销售额",
  "查询华东地区 2025 年第一季度销售额最高的前 5 个商品",
  "查询上个月抖音渠道女装品类的 GMV",
];

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "Vite /api proxy";

function makeId() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function upsertStep(steps: StepState[] = [], event: ProgressEvent | ToolProgressEvent) {
  const next = steps.filter((item) => item.step !== event.step);
  next.push({
    step: event.step,
    status: event.status,
    updatedAt: Date.now(),
  });
  return next;
}

const stepLabels: Record<string, string> = {
  route_by_pending_query: "检查待处理问数",
  classify_intent: "识别意图",
  rewrite_followup: "改写追问",
  normal_chat: "生成回复",
  call_ask_agent: "调用 askAgent",
  respond_clarification: "生成澄清问题",
};

function formatStepName(step: string) {
  return stepLabels[step] ?? step;
}

function getRowsFromToolResult(data: unknown) {
  if (data && typeof data === "object" && "rows" in data) {
    return (data as { rows?: unknown }).rows ?? [];
  }
  return data;
}

function getSqlFromToolResult(data: unknown) {
  if (data && typeof data === "object" && "sql" in data) {
    return (data as { sql?: string }).sql;
  }
  return undefined;
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sessionId, setSessionId] = useState(makeId);
  const [activeController, setActiveController] = useState<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const isStreaming = Boolean(activeController);
  const canSubmit = draft.trim().length > 0 && !isStreaming;

  const completedCount = useMemo(
    () => messages.filter((message) => message.role === "assistant" && message.status === "done").length,
    [messages],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  const startQuery = async (rawQuery = draft) => {
    const query = rawQuery.trim();
    if (!query || isStreaming) return;

    const userMessage: ChatMessage = {
      id: makeId(),
      role: "user",
      content: query,
      createdAt: Date.now(),
    };

    const assistantId = makeId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      activity: "正在连接 mainAgent...",
      createdAt: Date.now(),
      status: "streaming",
      steps: [],
    };

    const controller = new AbortController();
    setActiveController(controller);
    setDraft("");
    setMessages((current) => [...current, userMessage, assistantMessage]);

    const onEvent = (event: AgentEvent) => {
      setMessages((current) =>
        current.map((message) => {
          if (message.id !== assistantId) return message;

          if (event.type === "progress") {
            return {
              ...message,
              activity:
                event.status === "running"
                  ? `正在执行：${formatStepName(event.step)}`
                  : event.status === "success"
                    ? `${formatStepName(event.step)}完成`
                    : `${formatStepName(event.step)}失败`,
              steps: upsertStep(message.steps, event),
            };
          }

          if (event.type === "tool_progress") {
            return {
              ...message,
              tool: event.tool,
              activity:
                event.status === "running"
                  ? `${event.tool} 正在执行：${formatStepName(event.step)}`
                  : event.status === "success"
                    ? `${event.tool} 已完成：${formatStepName(event.step)}`
                    : `${event.tool} 执行失败：${formatStepName(event.step)}`,
              steps: upsertStep(message.steps, event),
            };
          }

          if (event.type === "agent_start") {
            return {
              ...message,
              activity: `${event.agent} 正在处理你的消息...`,
            };
          }

          if (event.type === "intent") {
            return {
              ...message,
              intent: event.intent,
              activity: `mainAgent 已识别意图：${event.intent}`,
            };
          }

          if (event.type === "followup_rewrite") {
            return {
              ...message,
              activity:
                event.status === "resolved" || event.status === "independent"
                  ? "追问已整理为完整问题"
                  : event.status === "clarify"
                    ? "需要补充查询条件"
                    : "追问状态已更新",
            };
          }

          if (event.type === "clarification") {
            return {
              ...message,
              content: event.question,
              activity: `等待澄清（${event.round}/${event.max_rounds}）`,
            };
          }

          if (event.type === "query_cancelled") {
            return {
              ...message,
              content: event.content,
              activity: "查询已取消",
            };
          }

          if (event.type === "rewrite_failed") {
            return {
              ...message,
              content: event.content,
              activity: "追问解析失败",
            };
          }

          if (event.type === "ask_failure") {
            return {
              ...message,
              content: event.message,
              activity: event.retryable ? "查询失败，可重试" : "查询失败",
              error: event.message,
            };
          }

          if (event.type === "message") {
            return {
              ...message,
              content: event.content,
            };
          }

          if (event.type === "message_delta") {
            return {
              ...message,
              content: `${message.content}${event.content}`,
            };
          }

          if (event.type === "tool_start") {
            return {
              ...message,
              tool: event.tool,
              activity: `正在调用 ${event.tool}...`,
            };
          }

          if (event.type === "tool_sql") {
            return {
              ...message,
              sql: event.sql,
            };
          }

          if (event.type === "tool_result") {
            return {
              ...message,
              tool: event.tool,
              result: getRowsFromToolResult(event.data),
              sql: getSqlFromToolResult(event.data) ?? message.sql,
              activity: `${event.tool} 已返回结果`,
            };
          }

          if (event.type === "result") {
            return {
              ...message,
              status: "done",
              content: summarizeResult(event.data),
              activity: "处理完成",
              result: event.data,
            };
          }

          if (event.type === "memory_update") {
            return message;
          }

          if (event.type === "final") {
            return {
              ...message,
              status: "done",
              content: event.content || message.content,
              activity: "处理完成",
            };
          }

          if (event.type === "error") {
            return {
              ...message,
              status: "error",
              content: message.content || "这次请求没有成功。",
              activity: "处理失败",
              error: event.message,
            };
          }

          // New backend progress events must not turn a successful stream into an error.
          return message;
        }),
      );
    };

    try {
      await streamQuery(query, { sessionId, signal: controller.signal, onEvent });
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId && message.status === "streaming"
            ? {
                ...message,
                status: "done",
                content: message.content || "本轮处理完成。",
                activity: "处理完成",
              }
            : message,
        ),
      );
    } catch (error) {
      const isAbort = error instanceof DOMException && error.name === "AbortError";
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                status: isAbort ? "done" : "error",
                content: isAbort ? message.content || "已停止本次处理。" : message.content || "无法连接智能体接口。",
                activity: isAbort ? "已停止" : "处理失败",
                error: isAbort ? undefined : error instanceof Error ? error.message : String(error),
              }
            : message,
        ),
      );
    } finally {
      setActiveController(null);
    }
  };

  const stopQuery = () => {
    activeController?.abort();
  };

  const clearConversation = () => {
    if (isStreaming) return;
    setMessages([]);
    setDraft("");
    setSessionId(makeId());
  };

  return (
    <div className="h-dvh overflow-hidden bg-parchment text-ink">
      <div className="pointer-events-none fixed inset-0 bg-[linear-gradient(90deg,rgba(32,32,29,0.045)_1px,transparent_1px),linear-gradient(rgba(32,32,29,0.035)_1px,transparent_1px)] bg-[size:48px_48px]" />
      <div className="pointer-events-none fixed inset-0 grain" />

      <div className="relative grid h-full min-h-0 overflow-hidden lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="hidden min-h-0 border-r border-ink/10 bg-[#efe6d8]/85 backdrop-blur lg:flex lg:flex-col">
          <div className="border-b border-ink/10 px-5 py-5">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center bg-ink text-parchment">
                <BarChart3 className="h-5 w-5" aria-hidden="true" />
              </div>
              <div>
                <div className="text-base font-semibold tracking-[0.02em]">电商问数</div>
                <div className="text-xs text-ink/50">shopkeeper-agent</div>
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
            <button
              type="button"
              onClick={clearConversation}
              disabled={isStreaming}
              className="flex h-11 w-full items-center justify-center gap-2 bg-ink text-sm font-semibold text-parchment transition hover:bg-soot disabled:cursor-not-allowed disabled:bg-ink/35"
            >
              <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
              新会话
            </button>

            <section>
              <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-[0.16em] text-ink/45">
                <History className="h-3.5 w-3.5" aria-hidden="true" />
                样例
              </div>
              <div className="space-y-2">
                {examples.map((example) => (
                  <button
                    key={example}
                    type="button"
                    disabled={isStreaming}
                    onClick={() => startQuery(example)}
                    className="w-full border border-ink/10 bg-white/42 px-3 py-3 text-left text-sm leading-5 text-ink/75 transition hover:border-moss/35 hover:bg-white/75 disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </section>
          </div>

          <div className="border-t border-ink/10 p-4">
            <div className="grid gap-2 text-xs text-ink/55">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2">
                  <Server className="h-3.5 w-3.5" aria-hidden="true" />
                  API
                </span>
                <span className="truncate font-mono">{API_BASE_URL}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2">
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                  完成
                </span>
                <span>{completedCount}</span>
              </div>
            </div>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/10 bg-parchment/88 px-4 backdrop-blur lg:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center bg-moss text-white lg:hidden">
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-ink">智能数据分析 Agent</div>
                <div className="truncate text-xs text-ink/45">FastAPI SSE / LangGraph</div>
              </div>
            </div>
            <button
              type="button"
              onClick={clearConversation}
              disabled={messages.length === 0 || isStreaming}
              className={cn(
                "grid h-9 w-9 place-items-center rounded-full text-ink/55 transition hover:bg-ink/5 hover:text-ink disabled:cursor-not-allowed disabled:opacity-35",
              )}
              title="清空"
              aria-label="清空"
            >
              <Eraser className="h-4 w-4" aria-hidden="true" />
            </button>
          </header>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {messages.length === 0 ? (
              <EmptyState examples={examples} onUseExample={(example) => setDraft(example)} />
            ) : (
              <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 lg:px-8">
                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-ink/10 bg-[#efe6d8]/45 px-4 py-2 text-center text-xs text-ink/45">
            <span className="inline-flex items-center gap-2">
              <Leaf className="h-3.5 w-3.5 text-moss" aria-hidden="true" />
              {isStreaming ? "运行中" : "就绪"}
            </span>
          </div>
          <Composer
            value={draft}
            disabled={!canSubmit}
            isStreaming={isStreaming}
            onChange={setDraft}
            onSubmit={() => startQuery()}
            onStop={stopQuery}
          />
        </main>
      </div>
    </div>
  );
}
