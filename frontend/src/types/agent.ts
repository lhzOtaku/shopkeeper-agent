/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
};

export type ToolProgressEvent = {
  type: "tool_progress";
  tool: string;
  step: string;
  status: ProgressStatus;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
};

export type AgentStartEvent = {
  type: "agent_start";
  agent: string;
};

export type IntentEvent = {
  type: "intent";
  intent: string;
  reason?: string;
  needs_clarification?: boolean;
  clarification_question?: string;
  source?: string;
};

export type FollowupRewriteEvent = {
  type: "followup_rewrite";
  status: "resolved" | "independent" | "clarify" | "cancelled" | "chat";
  original_message: string;
  resolved_query?: string | null;
  clarification_question?: string | null;
  reason?: string;
};

export type ClarificationEvent = {
  type: "clarification";
  question: string;
  round: number;
  max_rounds: number;
};

export type QueryCancelledEvent = {
  type: "query_cancelled";
  content: string;
};

export type RewriteFailedEvent = {
  type: "rewrite_failed";
  content: string;
  retryable: boolean;
};

export type AskFailureEvent = {
  type: "ask_failure";
  error_type: string;
  message: string;
  retryable: boolean;
};

export type MessageEvent = {
  type: "message";
  role: "assistant";
  content: string;
};

export type MessageDeltaEvent = {
  type: "message_delta";
  content: string;
};

export type ToolStartEvent = {
  type: "tool_start";
  tool: string;
  query?: string;
};

export type ToolSqlEvent = {
  type: "tool_sql";
  tool: string;
  sql: string;
};

export type ToolResultEvent = {
  type: "tool_result";
  tool: string;
  data: unknown;
};

export type MemoryUpdateEvent = {
  type: "memory_update";
  session_id: string;
  message_count: number;
  query_context_updated?: boolean;
  pending_phase?: string | null;
};

export type FinalEvent = {
  type: "final";
  content: string;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

export type AgentEvent =
  | ProgressEvent
  | ToolProgressEvent
  | ResultEvent
  | AgentStartEvent
  | IntentEvent
  | FollowupRewriteEvent
  | ClarificationEvent
  | QueryCancelledEvent
  | RewriteFailedEvent
  | AskFailureEvent
  | MessageEvent
  | MessageDeltaEvent
  | ToolStartEvent
  | ToolSqlEvent
  | ToolResultEvent
  | MemoryUpdateEvent
  | FinalEvent
  | ErrorEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  activity?: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  sql?: string;
  intent?: string;
  tool?: string;
  error?: string;
};
