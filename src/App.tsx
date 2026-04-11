import React, { startTransition, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
  ts?: string;
};

type Citation = {
  doc_id: string;
  snippet: string;
  score: number;
  metadata?: Record<string, unknown> | null;
};

type ToolCall = {
  id: string;
  name: string;
  status: "running" | "completed" | "error";
  description: string;
  inputLabel: string;
  input: string;
  outputLabel: string;
  output: string;
  meta: string[];
};

type AgentStepTrace = {
  stage: "thinking" | "acting" | "response" | "error";
  title: string;
  summary: string;
};

type AgentToolCallTrace = {
  name: string;
  status: "running" | "completed" | "error";
  input: string;
  output: string;
  latency_ms: number;
};

type AgentTrace = {
  run_id: string;
  status: "thinking" | "acting" | "response" | "error";
  task_type: "qa" | "summary";
  retrieval_summary?: string | null;
  rerank_summary?: string | null;
  summary_phase?: string | null;
  rewritten_queries: string[];
  steps: AgentStepTrace[];
  tool_calls: AgentToolCallTrace[];
};

type QAResponse = {
  session_id: string;
  answer: string;
  history: ChatMessage[];
  citations: Citation[];
  agent?: AgentTrace | null;
};

type DocUploadResponse = {
  doc_id: string;
  chunks_indexed: number;
};

type LoginState = {
  token: string | null;
  username: string | null;
};

type UserProfile = {
  username: string;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_login_at?: string | null;
  last_failed_login_at?: string | null;
  failed_login_attempts: number;
  locked_until?: string | null;
};

type AuditLogRecord = {
  event_type: string;
  username?: string | null;
  actor_username?: string | null;
  outcome: string;
  created_at: string;
  ip_address?: string | null;
  details: Record<string, unknown>;
};

type AgentTurn = {
  id: string;
  agentRunId?: string;
  userPrompt: string;
  taskType: "qa" | "summary";
  retrievalSummary: string;
  rerankSummary?: string;
  summaryPhase?: string;
  rewrittenQueries: string[];
  status: "thinking" | "acting" | "response";
  reasoningSummary: string;
  reasoningSteps: string[];
  toolCalls: ToolCall[];
  answer: string;
  citations: Citation[];
  createdAt: string;
  completedAt?: string;
  isPending: boolean;
};

type SessionRecord = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  preview: string;
  messageCount: number;
  turns: AgentTurn[];
};

type SessionSummaryResponse = {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
  message_count: number;
};

type SessionDetailResponse = SessionSummaryResponse & {
  history: ChatMessage[];
};

type FeedbackState = "up" | "down" | null;

const STORAGE_KEY = "react-qa-token";
const USERNAME_STORAGE_KEY = "react-qa-token-username";
const FEEDBACK_STORAGE_KEY = "react-qa-feedback";
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

function apiUrl(path: string): string {
  if (API_BASE_URL) return `${API_BASE_URL}${path}`;
  return path;
}

async function readError(resp: Response): Promise<string> {
  const text = await resp.text();
  if (!text) return `请求失败：${resp.status}`;

  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    return text;
  }

  return text;
}

function loadFeedbackMap(): Record<string, FeedbackState> {
  try {
    const raw = window.localStorage.getItem(FEEDBACK_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, FeedbackState>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveFeedbackMap(feedback: Record<string, FeedbackState>): void {
  window.localStorage.setItem(FEEDBACK_STORAGE_KEY, JSON.stringify(feedback));
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function inferPendingTaskType(question: string): "qa" | "summary" {
  const normalized = question.trim().toLowerCase();
  const summaryMarkers = ["总结", "摘要", "概括", "综述", "summarize", "summary"];
  const documentScopeMarkers = ["全文", "文档", "文章", "内容", "整体", "这份", "这篇", "最近上传", "本文"];

  if (summaryMarkers.some((marker) => normalized.includes(marker))) {
    if (documentScopeMarkers.some((marker) => normalized.includes(marker))) {
      return "summary";
    }
    if (["总结", "请总结", "帮我总结", "做个总结"].includes(normalized)) {
      return "summary";
    }
  }

  return "qa";
}

function createReasoningSummary(question: string, citations: Citation[]): string {
  if (citations.length > 0) {
    return `已结合 ${citations.length} 条知识片段，对“${question.slice(0, 32)}”完成整理与回答。`;
  }
  return `已分析问题“${question.slice(0, 32)}”，并基于当前上下文生成回答。`;
}

function createReasoningSteps(question: string, citations: Citation[]): string[] {
  const steps = [`识别问题意图：${question}`];
  if (citations.length > 0) {
    steps.push(`从知识库中命中 ${citations.length} 条相关片段。`);
    steps.push("对结果做相关性排序，并生成最终回答。");
  } else {
    steps.push("当前没有附带外部引用，因此主要依据上下文回答。");
  }
  return steps;
}

function buildToolCalls(question: string, citations: Citation[], topK: number, loading: boolean): ToolCall[] {
  if (loading) {
    return [
      {
        id: "pending-retrieval",
        name: "知识检索",
        status: "running",
        description: "正在搜索知识库中的相关内容。",
        inputLabel: "输入",
        input: question,
        outputLabel: "输出",
        output: "正在等待检索结果...",
        meta: [`Top K=${topK}`],
      },
    ];
  }

  return [
    {
      id: "completed-retrieval",
      name: "知识检索",
      status: "completed",
      description: "已返回当前回答使用的引用片段。",
      inputLabel: "输入",
      input: question,
      outputLabel: "输出",
      output:
        citations.length > 0
          ? citations.slice(0, 3).map((item) => `[${item.doc_id}] ${item.snippet}`).join("\n\n")
          : "当前回答没有附带引用片段。",
      meta: [`Top K=${topK}`, `命中=${citations.length}`],
    },
  ];
}

function mapAgentStatus(status: AgentTrace["status"]): AgentTurn["status"] {
  if (status === "acting") return "acting";
  return status === "response" ? "response" : "thinking";
}

function buildReasoningStepsFromTrace(trace?: AgentTrace | null, fallback: string[] = []): string[] {
  if (!trace || trace.steps.length === 0) return fallback;
  return trace.steps.map((step) => `${step.title}：${step.summary}`);
}

function buildReasoningSummaryFromTrace(trace?: AgentTrace | null, fallback = ""): string {
  if (!trace || trace.steps.length === 0) return fallback;
  return trace.steps[trace.steps.length - 1]?.summary ?? fallback;
}

function buildToolCallsFromTrace(trace?: AgentTrace | null): ToolCall[] {
  if (!trace || trace.tool_calls.length === 0) return [];
  return trace.tool_calls.map((tool, index) => ({
    id: `${trace.run_id}-${index}`,
    name: tool.name,
    status: tool.status,
    description: `延迟 ${tool.latency_ms}ms`,
    inputLabel: "输入",
    input: tool.input,
    outputLabel: "输出",
    output: tool.output,
    meta: [`Latency=${tool.latency_ms}ms`],
  }));
}

function createPendingTurn(question: string, topK: number): AgentTurn {
  const createdAt = new Date().toISOString();
  return {
    id: `turn-${Date.now()}`,
    userPrompt: question,
    taskType: inferPendingTaskType(question),
    retrievalSummary: "",
    rerankSummary: undefined,
    summaryPhase: undefined,
    rewrittenQueries: [],
    status: "thinking",
    reasoningSummary: "正在分析问题并准备检索知识库。",
    reasoningSteps: ["分析问题意图。", "匹配相关知识片段。", "组织最终回答。"],
    toolCalls: buildToolCalls(question, [], topK, true),
    answer: "",
    citations: [],
    createdAt,
    isPending: true,
  };
}

function upsertSessionRecord(
  sessions: SessionRecord[],
  sessionId: string,
  turns: AgentTurn[],
  fallbackTitle: string,
): SessionRecord[] {
  const titleSource = turns[0]?.userPrompt || fallbackTitle || "新对话";
  const title = titleSource.length > 26 ? `${titleSource.slice(0, 26)}...` : titleSource;
  const updatedAt = new Date().toISOString();
  const nextRecord: SessionRecord = {
    id: sessionId,
    title,
    createdAt: updatedAt,
    updatedAt,
    preview: turns[turns.length - 1]?.answer ?? "",
    messageCount: turns.length * 2,
    turns,
  };
  const filtered = sessions.filter((session) => session.id !== sessionId);
  return [nextRecord, ...filtered].sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

function mapSessionSummary(data: SessionSummaryResponse, turns: AgentTurn[] = []): SessionRecord {
  return {
    id: data.session_id,
    title: data.title,
    createdAt: data.created_at,
    updatedAt: data.updated_at,
    preview: data.last_message_preview,
    messageCount: data.message_count,
    turns,
  };
}

function hydrateTurnsFromHistory(history: ChatMessage[]): AgentTurn[] {
  const turns: AgentTurn[] = [];
  let pendingUser: ChatMessage | null = null;

  history.forEach((message, index) => {
    if (message.role === "user") {
      pendingUser = message;
      return;
    }
    if (message.role !== "assistant") return;

    const createdAt = pendingUser?.ts ?? message.ts ?? new Date().toISOString();
    const userPrompt = pendingUser?.content ?? "历史会话";
    turns.push({
      id: `history-turn-${index}`,
      userPrompt,
      taskType: inferPendingTaskType(userPrompt),
      retrievalSummary: "",
      rerankSummary: undefined,
      summaryPhase: undefined,
      rewrittenQueries: [],
      status: "response",
      reasoningSummary: "这是从后端同步的历史会话。",
      reasoningSteps: [],
      toolCalls: [],
      answer: message.content,
      citations: [],
      createdAt,
      completedAt: message.ts ?? createdAt,
      isPending: false,
    });
    pendingUser = null;
  });

  return turns;
}

function renderRichText(content: string): React.ReactNode {
  const segments = content.split(/```/);

  return segments.map((segment, index) => {
    if (index % 2 === 1) {
      const [firstLine, ...rest] = segment.split("\n");
      const language = firstLine.trim() || "text";
      const code = rest.join("\n").replace(/\n$/, "");
      return (
        <div key={`code-${index}`} className="code-block">
          <div className="code-header">{language}</div>
          <pre>
            <code>{code}</code>
          </pre>
        </div>
      );
    }

    return segment
      .split(/\n{2,}/)
      .filter(Boolean)
      .map((paragraph, paragraphIndex) => (
        <p key={`paragraph-${index}-${paragraphIndex}`}>{paragraph}</p>
      ));
  });
}

export const App: React.FC = () => {
  const [login, setLogin] = useState<LoginState>(() => ({
    token: window.localStorage.getItem(STORAGE_KEY),
    username: window.localStorage.getItem(USERNAME_STORAGE_KEY),
  }));
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [turns, setTurns] = useState<AgentTurn[]>([]);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [adminView, setAdminView] = useState<"overview" | "users" | "audit">("overview");
  const [managedUsers, setManagedUsers] = useState<UserProfile[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogRecord[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminSearch, setAdminSearch] = useState("");
  const [newUserUsername, setNewUserUsername] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState<"user" | "admin">("user");
  const [selectedUsers, setSelectedUsers] = useState<string[]>([]);
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>(() => loadFeedbackMap());
  const [expandedReasoning, setExpandedReasoning] = useState<Record<string, boolean>>({});
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [sessionSearch, setSessionSearch] = useState("");
  const [input, setInput] = useState("");
  const [topK, setTopK] = useState(4);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploadHint, setUploadHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requestStartedAt, setRequestStartedAt] = useState<number | null>(null);
  const [nowTick, setNowTick] = useState(Date.now());
  const [streamingTurnId, setStreamingTurnId] = useState<string | null>(null);
  const [streamedChars, setStreamedChars] = useState(0);

  const deferredSessionSearch = useDeferredValue(sessionSearch);
  const deferredAdminSearch = useDeferredValue(adminSearch);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const conversationRef = useRef<HTMLDivElement | null>(null);

  const isLoggedIn = useMemo(() => Boolean(login.token), [login.token]);
  const isAdmin = useMemo(() => profile?.role === "admin", [profile?.role]);

  const filteredSessions = useMemo(() => {
    const query = deferredSessionSearch.trim().toLowerCase();
    if (!query) return sessions;
    return sessions.filter((session) => session.title.toLowerCase().includes(query));
  }, [deferredSessionSearch, sessions]);

  const selectedTurn = useMemo(
    () => turns.find((turn) => turn.id === selectedTurnId) ?? turns[turns.length - 1] ?? null,
    [selectedTurnId, turns],
  );

  const filteredManagedUsers = useMemo(() => {
    const query = deferredAdminSearch.trim().toLowerCase();
    if (!query) return managedUsers;
    return managedUsers.filter(
      (user) =>
        user.username.toLowerCase().includes(query) ||
        user.role.toLowerCase().includes(query),
    );
  }, [deferredAdminSearch, managedUsers]);

  useEffect(() => {
    saveFeedbackMap(feedback);
  }, [feedback]);

  useEffect(() => {
    if (!loading) return;
    const timer = window.setInterval(() => setNowTick(Date.now()), 240);
    return () => window.clearInterval(timer);
  }, [loading]);

  useEffect(() => {
    if (!streamingTurnId) return;
    const turn = turns.find((item) => item.id === streamingTurnId);
    if (!turn) {
      setStreamingTurnId(null);
      setStreamedChars(0);
      return;
    }
    if (streamedChars >= turn.answer.length) {
      setStreamingTurnId(null);
      return;
    }
    const timer = window.setTimeout(() => {
      setStreamedChars((current) => Math.min(turn.answer.length, current + Math.max(4, Math.ceil(turn.answer.length / 48))));
    }, 18);
    return () => window.clearTimeout(timer);
  }, [streamedChars, streamingTurnId, turns]);

  useEffect(() => {
    if (!conversationRef.current) return;
    conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
  }, [turns, streamedChars, loading]);

  useEffect(() => {
    if (!isLoggedIn) {
      setSessionId(null);
      setMessages([]);
      setTurns([]);
      setSessions([]);
      setProfile(null);
      setManagedUsers([]);
      setAuditLogs([]);
      setSelectedUsers([]);
      setAdminView("overview");
      setUploadHint(null);
      setSelectedTurnId(null);
    }
  }, [isLoggedIn]);

  const refreshSessions = useCallback(async () => {
    if (!login.token) return;
    const resp = await fetch(apiUrl("/api/v1/chat/sessions"), {
      headers: { Authorization: `Bearer ${login.token}` },
    });
    if (!resp.ok) throw new Error(await readError(resp));
    const data = (await resp.json()) as SessionSummaryResponse[];
    setSessions((current) => {
      const turnsById = new Map(current.map((session) => [session.id, session.turns]));
      return data.map((item) => mapSessionSummary(item, turnsById.get(item.session_id) ?? []));
    });
  }, [login.token]);

  const refreshProfile = useCallback(async () => {
    if (!login.token) return;
    const resp = await fetch(apiUrl("/api/v1/auth/me"), {
      headers: { Authorization: `Bearer ${login.token}` },
    });
    if (!resp.ok) throw new Error(await readError(resp));
    const data = (await resp.json()) as UserProfile;
    setProfile(data);
  }, [login.token]);

  const refreshAdminUsers = useCallback(async () => {
    if (!login.token) return;
    const resp = await fetch(apiUrl("/api/v1/auth/users"), {
      headers: { Authorization: `Bearer ${login.token}` },
    });
    if (!resp.ok) throw new Error(await readError(resp));
    const data = (await resp.json()) as UserProfile[];
    setManagedUsers(data);
  }, [login.token]);

  const refreshAuditLogs = useCallback(async () => {
    if (!login.token) return;
    const resp = await fetch(apiUrl("/api/v1/auth/audit-logs?limit=50"), {
      headers: { Authorization: `Bearer ${login.token}` },
    });
    if (!resp.ok) throw new Error(await readError(resp));
    const data = (await resp.json()) as AuditLogRecord[];
    setAuditLogs(data);
  }, [login.token]);

  useEffect(() => {
    if (!isLoggedIn) return;
    void refreshSessions().catch((err) => {
      setError(err instanceof Error ? err.message : "会话同步失败");
    });
  }, [isLoggedIn, refreshSessions]);

  useEffect(() => {
    if (!isLoggedIn) return;
    void refreshProfile().catch((err) => {
      setError(err instanceof Error ? err.message : "用户信息加载失败");
    });
  }, [isLoggedIn, refreshProfile]);

  useEffect(() => {
    if (!isLoggedIn || !isAdmin) return;
    void refreshAdminUsers().catch((err) => {
      setError(err instanceof Error ? err.message : "用户列表加载失败");
    });
    void refreshAuditLogs().catch((err) => {
      setError(err instanceof Error ? err.message : "审计日志加载失败");
    });
  }, [isAdmin, isLoggedIn, refreshAdminUsers, refreshAuditLogs]);

  useEffect(() => {
    setSelectedUsers((current) => current.filter((username) => managedUsers.some((user) => user.username === username)));
  }, [managedUsers]);

  const currentPendingStage = useMemo(() => {
    if (!loading || requestStartedAt === null) return null;
    const elapsed = nowTick - requestStartedAt;
    if (elapsed < 1400) return "thinking";
    if (elapsed < 3000) return "acting";
    return "response";
  }, [loading, nowTick, requestStartedAt]);

  useEffect(() => {
    if (!currentPendingStage) return;
    setTurns((current) =>
      current.map((turn) => (turn.isPending ? { ...turn, status: currentPendingStage } : turn)),
    );
  }, [currentPendingStage]);

  const persistConversation = useCallback((nextSessionId: string, nextTurns: AgentTurn[], titleSeed: string) => {
    setSessions((current) => upsertSessionRecord(current, nextSessionId, nextTurns, titleSeed));
    void refreshSessions().catch(() => undefined);
  }, [refreshSessions]);

  const doLogin = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      try {
        const resp = await fetch(apiUrl("/api/v1/auth/login"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });
        if (!resp.ok) throw new Error(await readError(resp));
        const data = (await resp.json()) as { access_token: string };
        window.localStorage.setItem(STORAGE_KEY, data.access_token);
        window.localStorage.setItem(USERNAME_STORAGE_KEY, username);
        setLogin({ token: data.access_token, username });
      } catch (err) {
        setError(err instanceof Error ? err.message : "登录失败");
      }
    },
    [username, password],
  );

  const doRegister = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      try {
        const registerResp = await fetch(apiUrl("/api/v1/auth/register"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password, role: "user" }),
        });
        if (!registerResp.ok) throw new Error(await readError(registerResp));
        const loginResp = await fetch(apiUrl("/api/v1/auth/login"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });
        if (!loginResp.ok) throw new Error(await readError(loginResp));
        const data = (await loginResp.json()) as { access_token: string };
        window.localStorage.setItem(STORAGE_KEY, data.access_token);
        window.localStorage.setItem(USERNAME_STORAGE_KEY, username);
        setLogin({ token: data.access_token, username });
      } catch (err) {
        setError(err instanceof Error ? err.message : "注册失败");
      }
    },
    [username, password],
  );

  const logout = useCallback(() => {
    window.localStorage.removeItem(STORAGE_KEY);
    window.localStorage.removeItem(USERNAME_STORAGE_KEY);
    setLogin({ token: null, username: null });
    setSessionId(null);
    setMessages([]);
    setTurns([]);
    setUploadHint(null);
    setSelectedTurnId(null);
  }, []);

  const handleCreateUser = useCallback(async () => {
    if (!login.token || !newUserUsername.trim() || !newUserPassword.trim()) return;
    setAdminLoading(true);
    setError(null);
    try {
      const resp = await fetch(apiUrl("/api/v1/auth/users"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${login.token}`,
        },
        body: JSON.stringify({
          username: newUserUsername.trim(),
          password: newUserPassword,
          role: newUserRole,
        }),
      });
      if (!resp.ok) throw new Error(await readError(resp));
      setNewUserUsername("");
      setNewUserPassword("");
      setNewUserRole("user");
      await refreshAdminUsers();
      await refreshAuditLogs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建用户失败");
    } finally {
      setAdminLoading(false);
    }
  }, [login.token, newUserPassword, newUserRole, newUserUsername, refreshAdminUsers, refreshAuditLogs]);

  const handleToggleUserActive = useCallback(async (user: UserProfile) => {
    if (!login.token) return;
    setAdminLoading(true);
    setError(null);
    try {
      const resp = await fetch(apiUrl(`/api/v1/auth/users/${user.username}`), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${login.token}`,
        },
        body: JSON.stringify({ is_active: !user.is_active }),
      });
      if (!resp.ok) throw new Error(await readError(resp));
      await refreshAdminUsers();
      await refreshAuditLogs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新用户状态失败");
    } finally {
      setAdminLoading(false);
    }
  }, [login.token, refreshAdminUsers, refreshAuditLogs]);

  const handleDeleteManagedUser = useCallback(async (targetUsername: string) => {
    if (!login.token) return;
    setAdminLoading(true);
    setError(null);
    try {
      const resp = await fetch(apiUrl(`/api/v1/auth/users/${targetUsername}`), {
        method: "DELETE",
        headers: {
          Authorization: `Bearer ${login.token}`,
        },
      });
      if (!resp.ok) throw new Error(await readError(resp));
      setSelectedUsers((current) => current.filter((item) => item !== targetUsername));
      await refreshAdminUsers();
      await refreshAuditLogs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除用户失败");
    } finally {
      setAdminLoading(false);
    }
  }, [login.token, refreshAdminUsers, refreshAuditLogs]);

  const handleToggleSelectedUser = useCallback((targetUsername: string) => {
    setSelectedUsers((current) =>
      current.includes(targetUsername)
        ? current.filter((item) => item !== targetUsername)
        : [...current, targetUsername],
    );
  }, []);

  const handleBulkDeleteUsers = useCallback(async () => {
    if (!login.token || selectedUsers.length === 0) return;
    setAdminLoading(true);
    setError(null);
    try {
      const resp = await fetch(apiUrl("/api/v1/auth/users/bulk-delete"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${login.token}`,
        },
        body: JSON.stringify({ usernames: selectedUsers }),
      });
      if (!resp.ok) throw new Error(await readError(resp));
      setSelectedUsers([]);
      await refreshAdminUsers();
      await refreshAuditLogs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量删除失败");
    } finally {
      setAdminLoading(false);
    }
  }, [login.token, refreshAdminUsers, refreshAuditLogs, selectedUsers]);

  const uploadDocument = useCallback(
    async (file: File) => {
      if (!login.token) {
        setError("请先登录");
        return;
      }

      setUploading(true);
      setUploadHint(null);
      setError(null);
      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append(
          "metadata_json",
          JSON.stringify({
            filename: file.name,
            source: "chat_upload",
            uploaded_at: new Date().toISOString(),
          }),
        );

        const resp = await fetch(apiUrl("/api/v1/docs/upload"), {
          method: "POST",
          headers: {
            Authorization: `Bearer ${login.token}`,
          },
          body: formData,
        });
        if (!resp.ok) throw new Error(await readError(resp));
        const data = (await resp.json()) as DocUploadResponse;
        setUploadHint(`已完成 ${file.name} 的索引，文档 ID：${data.doc_id}，共 ${data.chunks_indexed} 个切片。`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "上传失败");
      } finally {
        setUploading(false);
      }
    },
    [login.token],
  );

  const submitQuestion = useCallback(
    async (question: string) => {
      if (!question.trim() || !login.token || loading) return;

      const prompt = question.trim();
      const pendingTurn = createPendingTurn(prompt, topK);

      setError(null);
      setLoading(true);
      setRequestStartedAt(Date.now());
      setTurns((current) => [...current, pendingTurn]);
      setSelectedTurnId(pendingTurn.id);
      setInput("");

      try {
        setMessages((prev) => [...prev, { role: "user", content: prompt }]);

        const resp = await fetch(apiUrl("/api/v1/chat/qa"), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${login.token}`,
          },
          body: JSON.stringify({
            message: prompt,
            session_id: sessionId,
            top_k: topK,
          }),
        });
        if (!resp.ok) throw new Error(await readError(resp));

        const data = (await resp.json()) as QAResponse;
        const derivedSummary = createReasoningSummary(prompt, data.citations);
        const derivedSteps = createReasoningSteps(prompt, data.citations);
        const tracedToolCalls = buildToolCallsFromTrace(data.agent);
        const finalTurn: AgentTurn = {
          ...pendingTurn,
          agentRunId: data.agent?.run_id ?? undefined,
          taskType: data.agent?.task_type ?? "qa",
          retrievalSummary: data.agent?.retrieval_summary ?? "",
          rerankSummary: data.agent?.rerank_summary ?? undefined,
          summaryPhase: data.agent?.summary_phase ?? undefined,
          rewrittenQueries: data.agent?.rewritten_queries ?? [],
          status: data.agent ? mapAgentStatus(data.agent.status) : "response",
          reasoningSummary: buildReasoningSummaryFromTrace(data.agent, derivedSummary),
          reasoningSteps: buildReasoningStepsFromTrace(data.agent, derivedSteps),
          toolCalls:
            tracedToolCalls.length > 0 ? tracedToolCalls : buildToolCalls(prompt, data.citations, topK, false),
          answer: data.answer,
          citations: data.citations,
          completedAt: new Date().toISOString(),
          isPending: false,
        };

        setSessionId(data.session_id);
        setMessages(data.history);
        setTurns((current) => {
          const resolvedTurns = [...current.filter((turn) => turn.id !== pendingTurn.id), finalTurn];
          persistConversation(data.session_id, resolvedTurns, prompt);
          return resolvedTurns;
        });
        setStreamingTurnId(finalTurn.id);
        setStreamedChars(0);
      } catch (err) {
        setTurns((current) =>
          current.map((turn) =>
            turn.id === pendingTurn.id
              ? {
                  ...turn,
                  status: "response",
                  reasoningSummary: "请求在生成回答前失败了。",
                  answer: err instanceof Error ? err.message : "请求失败",
                  completedAt: new Date().toISOString(),
                  isPending: false,
                }
              : turn,
          ),
        );
        setError(err instanceof Error ? err.message : "提问失败");
      } finally {
        setLoading(false);
        setRequestStartedAt(null);
      }
    },
    [loading, login.token, persistConversation, sessionId, topK],
  );

  const sendQuestion = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      await submitQuestion(input);
    },
    [input, submitQuestion],
  );

  const handleRetry = useCallback(
    async (prompt: string) => {
      await submitQuestion(prompt);
    },
    [submitQuestion],
  );

  const handleFileInput = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) await uploadDocument(file);
      e.target.value = "";
    },
    [uploadDocument],
  );

  const handleDrop = useCallback(
    async (e: React.DragEvent<HTMLDivElement | HTMLElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      if (file) await uploadDocument(file);
    },
    [uploadDocument],
  );

  const handleSessionSelect = useCallback(async (record: SessionRecord) => {
    if (!login.token) return;
    try {
      const resp = await fetch(apiUrl(`/api/v1/chat/sessions/${record.id}`), {
        headers: { Authorization: `Bearer ${login.token}` },
      });
      if (!resp.ok) throw new Error(await readError(resp));
      const detail = (await resp.json()) as SessionDetailResponse;
      const hydratedTurns = hydrateTurnsFromHistory(detail.history);
      startTransition(() => {
        setSessionId(detail.session_id);
        setTurns(hydratedTurns);
        setMessages(detail.history);
        setSelectedTurnId(hydratedTurns[hydratedTurns.length - 1]?.id ?? null);
        setSessions((current) =>
          current.map((session) =>
            session.id === detail.session_id ? mapSessionSummary(detail, hydratedTurns) : session,
          ),
        );
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "会话加载失败");
    }
  }, [login.token]);

  const handleNewChat = useCallback(() => {
    setSessionId(null);
    setMessages([]);
    setTurns([]);
    setSelectedTurnId(null);
    setInput("");
    setError(null);
  }, []);

  const handleDeleteSession = useCallback(
    async (recordId: string) => {
      if (!login.token) return;
      try {
        const resp = await fetch(apiUrl(`/api/v1/chat/sessions/${recordId}`), {
          method: "DELETE",
          headers: { Authorization: `Bearer ${login.token}` },
        });
        if (!resp.ok) throw new Error(await readError(resp));
        setSessions((current) => current.filter((session) => session.id !== recordId));
        if (recordId === sessionId) {
          setSessionId(null);
          setMessages([]);
          setTurns([]);
          setSelectedTurnId(null);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "删除会话失败");
      }
    },
    [login.token, sessionId],
  );

  const toggleReasoning = useCallback((turnId: string) => {
    setExpandedReasoning((current) => ({ ...current, [turnId]: !current[turnId] }));
  }, []);

  const setTurnFeedback = useCallback(
    async (turn: AgentTurn, value: FeedbackState) => {
      const nextValue = feedback[turn.id] === value ? null : value;
      setFeedback((current) => ({ ...current, [turn.id]: nextValue }));

      if (!nextValue || !login.token || !sessionId) return;

      try {
        const resp = await fetch(apiUrl("/api/v1/feedback"), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${login.token}`,
          },
          body: JSON.stringify({
            session_id: sessionId,
            run_id: turn.agentRunId ?? null,
            turn_id: turn.id,
            task_type: turn.taskType,
            feedback: nextValue,
            question: turn.userPrompt,
            answer: turn.answer,
          }),
        });
        if (!resp.ok) throw new Error(await readError(resp));
      } catch (err) {
        setError(err instanceof Error ? err.message : "反馈提交失败");
      }
    },
    [feedback, login.token, sessionId],
  );

  const handleCopy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      setError("复制失败");
    }
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top compact">
          <div className="auth-switch">
            <button
              type="button"
              className={`ghost-button auth-tab${authMode === "login" ? " is-on" : ""}`}
              onClick={() => setAuthMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              className={`ghost-button auth-tab${authMode === "register" ? " is-on" : ""}`}
              onClick={() => setAuthMode("register")}
            >
              注册
            </button>
          </div>
        </div>

        {isLoggedIn ? (
          <div className="user-strip">
            <span className="status-dot" />
            <span>{login.username ?? "已登录"}</span>
            <button onClick={logout} className="ghost-button" type="button">
              退出
            </button>
          </div>
        ) : (
          <form onSubmit={authMode === "login" ? doLogin : doRegister} className="login-card">
            <div className="panel-title">{authMode === "login" ? "账号登录" : "注册账号"}</div>
            <input type="text" placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
            <input type="password" placeholder="密码" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button type="submit" className="primary-button">
              {authMode === "login" ? "登录" : "注册并登录"}
            </button>
          </form>
        )}

        <div className="sidebar-section sidebar-history">
          <div className="panel-title">历史对话记录</div>
          <input
            className="search-input"
            type="text"
            placeholder="搜索会话"
            value={sessionSearch}
            onChange={(e) => setSessionSearch(e.target.value)}
          />
          <div className="session-list">
            {filteredSessions.length === 0 ? (
              <div className="empty-state compact">还没有保存的对话记录。</div>
            ) : (
              filteredSessions.map((session) => (
                <div
                  key={session.id}
                  className={`session-card${session.id === sessionId ? " is-active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleSessionSelect(session)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      handleSessionSelect(session);
                    }
                  }}
                >
                  <div className="session-card-top">
                    <span className="session-title">{session.title}</span>
                    <button
                      type="button"
                      className="session-delete"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteSession(session.id);
                      }}
                    >
                      删除
                    </button>
                  </div>
                  <div className="session-time">{formatTime(session.updatedAt)}</div>
                  <div className="session-preview">
                    {session.preview || session.turns[session.turns.length - 1]?.answer || "暂无内容"}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <button type="button" className="primary-button sidebar-new-chat" onClick={handleNewChat}>
          + 新添对话
        </button>
      </aside>

      <main className="main-stage">
        <header className="topbar topbar-hero">
          <div>
            <div className="eyebrow">Document Copilot</div>
            <h1>智能文档问答系统</h1>
            <p className="hero-copy">上传知识库文档后，在同一条会话中完成问答、总结与连续追问。</p>
          </div>
          <div className="topbar-controls">
            <label className="select-wrap">
              <span>Top K</span>
              <input type="number" min={1} max={20} value={topK} onChange={(e) => setTopK(Number(e.target.value) || 4)} />
            </label>
            {sessionId ? <div className="session-badge">会话 {sessionId.slice(0, 8)}</div> : null}
          </div>
        </header>

        <div className="workspace-grid">
          <section
            className={`conversation-panel composer-shell${dragOver ? " is-over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setDragOver(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setDragOver(false);
            }}
            onDrop={handleDrop}
          >
            <div className="conversation-scroll" ref={conversationRef}>
              {turns.length === 0 ? (
                <div className="empty-state large hero-empty">
                  <div className="empty-title">开始一段新的文档对话</div>
                  <div className="empty-copy">把文件拖拽到聊天框，或者点击上传文档，将知识库内容加入当前工作区。</div>
                </div>
              ) : (
                turns.map((turn) => {
                  const isExpanded = Boolean(expandedReasoning[turn.id]);
                  const answerText = turn.id === streamingTurnId ? turn.answer.slice(0, streamedChars) : turn.answer;
                  const stage = turn.isPending ? currentPendingStage ?? turn.status : turn.status;

                  return (
                    <article
                      key={turn.id}
                      className={`turn-card${selectedTurn?.id === turn.id ? " is-selected" : ""}`}
                      onClick={() => setSelectedTurnId(turn.id)}
                    >
                      <div className="user-bubble">
                        <div className="message-label">你</div>
                        <div className="message-body">{turn.userPrompt}</div>
                      </div>

                      <div className="agent-card">
                        <div className="agent-card-header">
                          <div>
                            <div className="message-label">系统</div>
                            <div className="agent-meta">{formatTime(turn.createdAt)}</div>
                          </div>
                          <div className="stage-rail">
                            <div className={`stage-pill${stage === "thinking" ? " is-active" : ""}`}>分析</div>
                            <div className={`stage-pill${stage === "acting" ? " is-active" : ""}`}>检索</div>
                            <div className={`stage-pill${stage === "response" ? " is-active" : ""}`}>回答</div>
                          </div>
                        </div>

                        <div className="reasoning-bar">
                          <div>
                            <div className="reasoning-title">本轮摘要</div>
                            <div className="reasoning-copy">{turn.reasoningSummary}</div>
                          </div>
                          <button type="button" className="ghost-button" onClick={() => toggleReasoning(turn.id)}>
                            {isExpanded ? "收起过程" : "查看过程"}
                          </button>
                        </div>

                        {isExpanded ? (
                          <div className="reasoning-steps">
                            {turn.reasoningSteps.map((step, index) => (
                              <div key={`${turn.id}-step-${index}`} className="reasoning-step">
                                <span className="step-index">{index + 1}</span>
                                <span>{step}</span>
                              </div>
                            ))}
                          </div>
                        ) : null}

                        <div className="response-card">
                          <div className="response-header">
                            <div className="response-title">回答</div>
                            {turn.isPending ? <div className="typing-indicator">正在生成</div> : null}
                          </div>
                          <div className="response-body">{renderRichText(answerText || (turn.isPending ? "正在生成..." : ""))}</div>

                          {turn.citations.length > 0 ? (
                            <div className="citation-list">
                              {turn.citations.map((citation) => (
                                <div key={`${turn.id}-${citation.doc_id}`} className="citation-card">
                                  <div className="citation-top">
                                    <span>{citation.doc_id}</span>
                                    <span>{citation.score.toFixed(2)}</span>
                                  </div>
                                  <div className="citation-snippet">{citation.snippet}</div>
                                </div>
                              ))}
                            </div>
                          ) : null}

                          <div className="feedback-bar">
                            <button
                              type="button"
                              className={`ghost-button${feedback[turn.id] === "up" ? " is-on" : ""}`}
                              onClick={() => void setTurnFeedback(turn, "up")}
                            >
                              赞
                            </button>
                            <button
                              type="button"
                              className={`ghost-button${feedback[turn.id] === "down" ? " is-on" : ""}`}
                              onClick={() => void setTurnFeedback(turn, "down")}
                            >
                              踩
                            </button>
                            <button type="button" className="ghost-button" onClick={() => handleRetry(turn.userPrompt)}>
                              重试
                            </button>
                            <button type="button" className="ghost-button" onClick={() => handleCopy(turn.answer)}>
                              复制
                            </button>
                          </div>
                        </div>
                      </div>
                    </article>
                  );
                })
              )}
            </div>

            <form onSubmit={sendQuestion} className="composer composer-dropzone">
              <input
                ref={fileInputRef}
                type="file"
                className="upload-input"
                accept=".txt,.md,.markdown,.csv,.json,.log,.pdf,.docx"
                onChange={handleFileInput}
              />
              <div className="composer-toolbar">
                <div className="upload-copy">
                  <div className="upload-title">{uploading ? "上传中..." : "将知识库文档拖拽到聊天框"}</div>
                  <div className="upload-subtitle">也可以点击上传文档，将文件加入当前检索工作区。</div>
                </div>
                <button type="button" className="ghost-button" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                  上传文档
                </button>
              </div>
              {uploadHint ? <div className="upload-hint">{uploadHint}</div> : null}
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="输入你的问题，例如：请总结这份文档，或者提取主要风险与待办事项。"
                rows={4}
                disabled={!isLoggedIn || loading}
              />
              <div className="composer-footer">
                <div className="quick-actions">
                  <button type="button" className="tag-chip" onClick={() => setInput("请总结最近上传的文档。")}>
                    总结文档
                  </button>
                  <button type="button" className="tag-chip" onClick={() => setInput("请提取文档中的关键风险和假设。")}>
                    风险假设
                  </button>
                  <button type="button" className="tag-chip" onClick={() => setInput("请列出当前上下文中的可执行下一步。")}>
                    下一步
                  </button>
                </div>
                <button type="submit" className="primary-button" disabled={!isLoggedIn || loading || !input.trim()}>
                  {loading ? "处理中..." : "发送"}
                </button>
              </div>
            </form>
          </section>

          <aside className="inspector-panel lite-panel">
            <div className="panel-title">会话概览</div>
            {selectedTurn ? (
              <>
                <div className="inspector-block">
                  <div className="inspector-label">当前模式</div>
                  <div className="inspector-value">{selectedTurn.taskType === "summary" ? "总结模式" : "问答模式"}</div>
                </div>
                <div className="inspector-block">
                  <div className="inspector-label">回答状态</div>
                  <div className="inspector-value">{selectedTurn.isPending ? "进行中" : "已完成"}</div>
                </div>
                <div className="inspector-block">
                  <div className="inspector-label">引用片段</div>
                  <div className="inspector-value">{selectedTurn.citations.length} 条</div>
                </div>
                {selectedTurn.summaryPhase ? (
                  <div className="inspector-block">
                    <div className="inspector-label">Summary 阶段</div>
                    <div className="inspector-value">{selectedTurn.summaryPhase}</div>
                  </div>
                ) : null}
                {selectedTurn.retrievalSummary ? (
                  <div className="inspector-block">
                    <div className="inspector-label">检索摘要</div>
                    <div className="inspector-value">{selectedTurn.retrievalSummary}</div>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="empty-state compact">当前还没有会话内容，先上传文档或发起提问。</div>
            )}
            {isAdmin ? (
              <>
                <div className="panel-title">管理员工作台</div>
                <div className="admin-tabs">
                  <button
                    type="button"
                    className={`ghost-button auth-tab${adminView === "users" ? " is-on" : ""}`}
                    onClick={() => setAdminView("users")}
                  >
                    用户管理
                  </button>
                  <button
                    type="button"
                    className={`ghost-button auth-tab${adminView === "audit" ? " is-on" : ""}`}
                    onClick={() => setAdminView("audit")}
                  >
                    审计日志
                  </button>
                  <button
                    type="button"
                    className={`ghost-button auth-tab${adminView === "overview" ? " is-on" : ""}`}
                    onClick={() => setAdminView("overview")}
                  >
                    会话概览
                  </button>
                </div>

                {adminView === "users" ? (
                  <div className="admin-panel">
                    <div className="admin-actions">
                      <input
                        className="search-input"
                        type="text"
                        placeholder="搜索用户"
                        value={adminSearch}
                        onChange={(e) => setAdminSearch(e.target.value)}
                      />
                      <div className="admin-form">
                        <input
                          type="text"
                          placeholder="新用户名"
                          value={newUserUsername}
                          onChange={(e) => setNewUserUsername(e.target.value)}
                        />
                        <input
                          type="password"
                          placeholder="初始密码"
                          value={newUserPassword}
                          onChange={(e) => setNewUserPassword(e.target.value)}
                        />
                        <select value={newUserRole} onChange={(e) => setNewUserRole(e.target.value as "user" | "admin")}>
                          <option value="user">user</option>
                          <option value="admin">admin</option>
                        </select>
                        <button type="button" className="primary-button" onClick={() => void handleCreateUser()} disabled={adminLoading}>
                          创建用户
                        </button>
                      </div>
                      <div className="admin-toolbar">
                        <span>已选 {selectedUsers.length} 个用户</span>
                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() => void handleBulkDeleteUsers()}
                          disabled={adminLoading || selectedUsers.length === 0}
                        >
                          批量删除
                        </button>
                        <button type="button" className="ghost-button" onClick={() => void refreshAdminUsers()} disabled={adminLoading}>
                          刷新
                        </button>
                      </div>
                    </div>
                    <div className="admin-list">
                      {filteredManagedUsers.map((user) => (
                        <div key={user.username} className="admin-card">
                          <label className="admin-card-top">
                            <input
                              type="checkbox"
                              checked={selectedUsers.includes(user.username)}
                              onChange={() => handleToggleSelectedUser(user.username)}
                            />
                            <div>
                              <div className="admin-card-title">
                                {user.username} <span className="role-badge">{user.role}</span>
                              </div>
                              <div className="admin-card-meta">
                                {user.is_active ? "启用中" : "已禁用"} · 失败 {user.failed_login_attempts} 次
                              </div>
                            </div>
                          </label>
                          <div className="admin-card-grid">
                            <div>最后登录：{user.last_login_at ? formatTime(user.last_login_at) : "暂无"}</div>
                            <div>最近失败：{user.last_failed_login_at ? formatTime(user.last_failed_login_at) : "暂无"}</div>
                            <div>锁定至：{user.locked_until ? formatTime(user.locked_until) : "未锁定"}</div>
                          </div>
                          <div className="admin-card-actions">
                            <button
                              type="button"
                              className="ghost-button"
                              onClick={() => void handleToggleUserActive(user)}
                              disabled={adminLoading || user.username === profile?.username}
                            >
                              {user.is_active ? "禁用" : "启用"}
                            </button>
                            <button
                              type="button"
                              className="ghost-button"
                              onClick={() => void handleDeleteManagedUser(user.username)}
                              disabled={adminLoading || user.username === profile?.username}
                            >
                              删除
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {adminView === "audit" ? (
                  <div className="admin-panel">
                    <div className="admin-toolbar">
                      <span>最近 50 条认证事件</span>
                      <button type="button" className="ghost-button" onClick={() => void refreshAuditLogs()} disabled={adminLoading}>
                        刷新日志
                      </button>
                    </div>
                    <div className="audit-list">
                      {auditLogs.map((item, index) => (
                        <div key={`${item.created_at}-${index}`} className="audit-card">
                          <div className="audit-card-top">
                            <strong>{item.event_type}</strong>
                            <span>{formatTime(item.created_at)}</span>
                          </div>
                          <div className="audit-card-meta">
                            目标：{item.username ?? "system"} · 操作人：{item.actor_username ?? "-"} · 结果：{item.outcome}
                          </div>
                          {item.ip_address ? <div className="audit-card-meta">IP：{item.ip_address}</div> : null}
                          {Object.keys(item.details ?? {}).length > 0 ? (
                            <pre className="audit-card-details">{JSON.stringify(item.details, null, 2)}</pre>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </>
            ) : null}
          </aside>
        </div>

        {error ? (
          <div className="toast error">
            <span>{error}</span>
          </div>
        ) : null}
      </main>
    </div>
  );
};

export default App;
