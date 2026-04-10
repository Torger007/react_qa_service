import React, {
  startTransition,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

type Citation = {
  doc_id: string;
  snippet: string;
  score: number;
  metadata?: Record<string, unknown> | null;
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
  updatedAt: string;
  turns: AgentTurn[];
};

type FeedbackState = "up" | "down" | null;

const STORAGE_KEY = "react-qa-token";
const SESSION_STORAGE_KEY = "react-qa-ui-sessions";
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
    // Fall back to raw text when response is not JSON.
  }

  return text;
}

function loadStoredSessions(): SessionRecord[] {
  try {
    const raw = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SessionRecord[];
    if (!Array.isArray(parsed)) return [];
    return parsed.map((session) => ({
      ...session,
      turns: Array.isArray(session.turns)
        ? session.turns.map((turn) => ({
            ...turn,
            taskType: turn.taskType ?? "qa",
            retrievalSummary: turn.retrievalSummary ?? "",
            rerankSummary: turn.rerankSummary ?? undefined,
            summaryPhase: turn.summaryPhase ?? undefined,
            rewrittenQueries: turn.rewrittenQueries ?? [],
          }))
        : [],
    }));
  } catch {
    return [];
  }
}

function saveStoredSessions(sessions: SessionRecord[]): void {
  window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessions));
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

function getToolStatusLabel(status: ToolCall["status"]): string {
  if (status === "running") return "执行中";
  if (status === "completed") return "已完成";
  return "失败";
}

function getCodeLanguageLabel(language: string): string {
  if (language === "text") return "文本";
  return language;
}

function createReasoningSummary(question: string, citations: Citation[]): string {
  if (citations.length > 0) {
    return `已检索到 ${citations.length} 条可用片段，完成证据排序，并围绕“${question.slice(0, 36)}”生成最终回答。`;
  }

  return `已分析问题、检查当前会话上下文，并围绕“${question.slice(0, 36)}”生成直接回答。`;
}

function createReasoningSteps(question: string, citations: Citation[]): string[] {
  const steps = [
    `已解析用户意图，并从问题中提取关键目标：${question}`,
    "已检查最近的对话上下文，保证回答连续性。",
  ];

  if (citations.length > 0) {
    steps.push(`已查询知识库，并筛选出 ${citations.length} 条相关片段。`);
    steps.push("已比较检索置信度，并选出最有用的证据片段。");
  } else {
    steps.push("当前没有附带外部检索结果，因此回答基于现有上下文生成。");
  }

  steps.push("已生成最终回答，并完成可读性整理。");
  return steps;
}

function inferPendingTaskType(question: string): "qa" | "summary" {
  const normalized = question.trim().toLowerCase();
  const summaryMarkers = [
    "总结",
    "摘要",
    "概括",
    "综述",
    "summarize",
    "summary",
  ];
  const documentScopeMarkers = [
    "全文",
    "文档",
    "文章",
    "内容",
    "整体",
    "这份",
    "这篇",
    "最近上传",
    "上面",
    "上述",
    "本文",
  ];

  if (summaryMarkers.some((marker) => normalized.includes(marker))) {
    if (documentScopeMarkers.some((marker) => normalized.includes(marker))) {
      return "summary";
    }
    if (
      normalized === "总结" ||
      normalized === "请总结" ||
      normalized === "请总结一下" ||
      normalized === "帮我总结" ||
      normalized === "做个总结"
    ) {
      return "summary";
    }
  }

  return "qa";
}

function buildToolCalls(question: string, citations: Citation[], topK: number, loading: boolean): ToolCall[] {
  if (loading) {
    return [
      {
        id: "tool-search-pending",
        name: "知识检索",
        status: "running",
        description: "正在搜索已建立索引的文档，补充回答依据。",
        inputLabel: "输入",
        input: question,
        outputLabel: "输出",
        output: "正在等待检索结果...",
        meta: [`检索数=${topK}`, "状态=执行中"],
      },
    ];
  }

  if (citations.length === 0) {
    return [
      {
        id: "tool-search-empty",
        name: "知识检索",
        status: "completed",
        description: "执行完成，但没有返回可引用片段。",
        inputLabel: "输入",
        input: question,
        outputLabel: "输出",
        output: "当前回答未从接口返回引用片段。",
        meta: [`检索数=${topK}`, "命中=0"],
      },
    ];
  }

  return [
    {
      id: "tool-search-completed",
      name: "知识检索",
      status: "completed",
      description: "已从知识库中提取支撑回答的片段。",
      inputLabel: "输入",
      input: question,
      outputLabel: "结果",
      output: citations
        .slice(0, 3)
        .map((item, index) => `${index + 1}. [${item.doc_id}] ${item.snippet}`)
        .join("\n\n"),
      meta: [`检索数=${topK}`, `命中=${citations.length}`],
    },
  ];
}

function mapAgentStatus(status: AgentTrace["status"]): AgentTurn["status"] {
  if (status === "acting") return "acting";
  return status === "response" ? "response" : "thinking";
}

function buildReasoningStepsFromTrace(trace?: AgentTrace | null, fallback: string[] = []): string[] {
  if (!trace || trace.steps.length === 0) return fallback;
  return trace.steps.map((step) => `${step.title}: ${step.summary}`);
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
    description: `latency=${tool.latency_ms}ms`,
    inputLabel: "输入",
    input: tool.input,
    outputLabel: "输出",
    output: tool.output,
    meta: [`latency=${tool.latency_ms}ms`],
  }));
}

function createPendingTurn(question: string, topK: number): AgentTurn {
  const createdAt = new Date().toISOString();
  return {
    id: `turn-${Date.now()}`,
    agentRunId: undefined,
    userPrompt: question,
    taskType: inferPendingTaskType(question),
    retrievalSummary: "",
    rerankSummary: undefined,
    status: "thinking",
    rewrittenQueries: [],
    reasoningSummary: "正在规划下一步动作，并准备启动检索。",
    reasoningSteps: [
      "正在检查用户请求的意图与缺失约束。",
      "正在为当前会话选择合适的检索路径。",
      "正在准备回答结构，等待进入生成阶段。",
    ],
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
  const titleSource = turns[0]?.userPrompt || fallbackTitle || "新会话";
  const title = titleSource.length > 42 ? `${titleSource.slice(0, 42)}...` : titleSource;
  const updatedAt = new Date().toISOString();
  const nextRecord: SessionRecord = { id: sessionId, title, updatedAt, turns };
  const filtered = sessions.filter((session) => session.id !== sessionId);
  return [nextRecord, ...filtered].sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function tokenizeCode(line: string, lang: string): React.ReactNode[] {
  const keywordsByLang: Record<string, string[]> = {
    js: ["const", "let", "var", "return", "if", "else", "async", "await", "function", "import", "from"],
    ts: ["const", "let", "var", "return", "if", "else", "async", "await", "function", "type", "interface"],
    jsx: ["const", "return", "if", "else", "function", "import", "from"],
    tsx: ["const", "return", "if", "else", "function", "import", "from", "type"],
    py: ["def", "return", "if", "else", "elif", "for", "while", "import", "from", "class", "async", "await"],
    python: ["def", "return", "if", "else", "elif", "for", "while", "import", "from", "class", "async", "await"],
    sql: ["select", "from", "where", "group", "by", "order", "limit", "insert", "into", "update", "delete"],
    bash: ["if", "then", "fi", "for", "do", "done", "echo", "export"],
  };

  const normalized = lang.toLowerCase();
  const keywords = keywordsByLang[normalized] ?? keywordsByLang.ts;
  const regex = new RegExp(
    [
      "(#.*$)",
      "(//.*$)",
      "('(?:[^'\\\\]|\\\\.)*')",
      '("(?:[^"\\\\]|\\\\.)*")',
      "\\b\\d+(?:\\.\\d+)?\\b",
      `\\b(?:${keywords.map(escapeRegExp).join("|")})\\b`,
    ].join("|"),
    normalized === "sql" ? "gi" : "g",
  );

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;

  for (const match of line.matchAll(regex)) {
    const value = match[0];
    const index = match.index ?? 0;

    if (index > lastIndex) {
      nodes.push(line.slice(lastIndex, index));
    }

    let className = "token-keyword";
    if (value.startsWith("'") || value.startsWith('"')) className = "token-string";
    else if (value.startsWith("#") || value.startsWith("//")) className = "token-comment";
    else if (/^\d/.test(value)) className = "token-number";

    nodes.push(
      <span key={`${index}-${value}`} className={className}>
        {value}
      </span>,
    );
    lastIndex = index + value.length;
  }

  if (lastIndex < line.length) {
    nodes.push(line.slice(lastIndex));
  }

  return nodes;
}

function renderRichText(content: string): React.ReactNode {
  const segments = content.split(/```/);

  return segments.map((segment, index) => {
    if (index % 2 === 1) {
      const [firstLine, ...rest] = segment.split("\n");
      const language = firstLine.trim() || "text";
      const code = rest.join("\n").replace(/\n$/, "");
      const lines = code.split("\n");

      return (
        <div key={`code-${index}`} className="code-block">
          <div className="code-header">
            <span>{getCodeLanguageLabel(language)}</span>
          </div>
          <pre>
            <code>
              {lines.map((line, lineIndex) => (
                <div key={`line-${lineIndex}`} className="code-line">
                  <span className="code-line-number">{lineIndex + 1}</span>
                  <span className="code-line-content">{tokenizeCode(line, language)}</span>
                </div>
              ))}
            </code>
          </pre>
        </div>
      );
    }

    return segment
      .split(/\n{2,}/)
      .filter(Boolean)
      .map((paragraph, paragraphIndex) => {
        const lines = paragraph.split("\n");
        return (
          <p key={`paragraph-${index}-${paragraphIndex}`}>
            {lines.map((line, lineIndex) => (
              <React.Fragment key={`line-${lineIndex}`}>
                {line}
                {lineIndex < lines.length - 1 ? <br /> : null}
              </React.Fragment>
            ))}
          </p>
        );
      });
  });
}

export const App: React.FC = () => {
  const [login, setLogin] = useState<LoginState>(() => {
    const token = window.localStorage.getItem(STORAGE_KEY);
    return { token };
  });
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [turns, setTurns] = useState<AgentTurn[]>([]);
  const [sessions, setSessions] = useState<SessionRecord[]>(() => loadStoredSessions());
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>(() => loadFeedbackMap());
  const [expandedReasoning, setExpandedReasoning] = useState<Record<string, boolean>>({});
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [sessionSearch, setSessionSearch] = useState("");
  const deferredSessionSearch = useDeferredValue(sessionSearch);
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

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const conversationRef = useRef<HTMLDivElement | null>(null);

  const isLoggedIn = useMemo(() => Boolean(login.token), [login.token]);

  const filteredSessions = useMemo(() => {
    const query = deferredSessionSearch.trim().toLowerCase();
    if (!query) return sessions;
    return sessions.filter((session) => session.title.toLowerCase().includes(query));
  }, [deferredSessionSearch, sessions]);

  const selectedTurn = useMemo(
    () => turns.find((turn) => turn.id === selectedTurnId) ?? turns[turns.length - 1] ?? null,
    [selectedTurnId, turns],
  );

  useEffect(() => {
    saveStoredSessions(sessions);
  }, [sessions]);

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
      setStreamedChars((current) => Math.min(turn.answer.length, current + Math.max(3, Math.ceil(turn.answer.length / 50))));
    }, 16);

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
      setUploadHint(null);
      setSelectedTurnId(null);
    }
  }, [isLoggedIn]);

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

  const persistConversation = useCallback(
    (nextSessionId: string, nextTurns: AgentTurn[], titleSeed: string) => {
      setSessions((current) => upsertSessionRecord(current, nextSessionId, nextTurns, titleSeed));
    },
    [],
  );

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
        if (!resp.ok) {
          throw new Error(await readError(resp));
        }
        const data = (await resp.json()) as { access_token: string };
        window.localStorage.setItem(STORAGE_KEY, data.access_token);
        setLogin({ token: data.access_token });
      } catch (err) {
        setError(err instanceof Error ? err.message : "登录失败");
      }
    },
    [username, password],
  );

  const logout = useCallback(() => {
    window.localStorage.removeItem(STORAGE_KEY);
    setLogin({ token: null });
    setSessionId(null);
    setMessages([]);
    setTurns([]);
    setUploadHint(null);
    setSelectedTurnId(null);
  }, []);

  const uploadDocument = useCallback(
    async (file: File) => {
      if (!login.token) {
        setError("请先登录");
        return;
      }

      setError(null);
      setUploadHint(null);
      setUploading(true);
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
        if (!resp.ok) {
          throw new Error(await readError(resp));
        }
        const data = (await resp.json()) as DocUploadResponse;
        setUploadHint(`已完成 ${file.name} 的索引，文档 ID：${data.doc_id}，共 ${data.chunks_indexed} 个分片。`);
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
        const userMsg: ChatMessage = { role: "user", content: prompt };
        setMessages((prev) => [...prev, userMsg]);

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
        if (!resp.ok) {
          throw new Error(await readError(resp));
        }

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
                  reasoningSummary: "请求在生成最终回答前失败。",
                  toolCalls: [
                    {
                      id: `${turn.id}-error`,
                      name: "知识检索",
                      status: "error",
                      description: "等待接口返回时发生错误。",
                      inputLabel: "输入",
                      input: prompt,
                      outputLabel: "错误",
                      output: err instanceof Error ? err.message : "未知错误",
                      meta: [`检索数=${topK}`, "状态=失败"],
                    },
                  ],
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
      if (file) {
        await uploadDocument(file);
      }
      e.target.value = "";
    },
    [uploadDocument],
  );

  const handleDrop = useCallback(
    async (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      if (file) {
        await uploadDocument(file);
      }
    },
    [uploadDocument],
  );

  const handleSessionSelect = useCallback((record: SessionRecord) => {
    startTransition(() => {
      setSessionId(record.id);
      setTurns(record.turns);
      setMessages(
        record.turns.flatMap((turn) => [
          { role: "user" as const, content: turn.userPrompt },
          { role: "assistant" as const, content: turn.answer },
        ]),
      );
      setSelectedTurnId(record.turns[record.turns.length - 1]?.id ?? null);
    });
  }, []);

  const handleNewChat = useCallback(() => {
    setSessionId(null);
    setMessages([]);
    setTurns([]);
    setSelectedTurnId(null);
    setInput("");
    setError(null);
  }, []);

  const toggleReasoning = useCallback((turnId: string) => {
    setExpandedReasoning((current) => ({ ...current, [turnId]: !current[turnId] }));
  }, []);

  const setTurnFeedback = useCallback(
    async (turn: AgentTurn, value: FeedbackState) => {
      const nextValue = feedback[turn.id] === value ? null : value;
      setFeedback((current) => ({
        ...current,
        [turn.id]: nextValue,
      }));

      if (!nextValue || !login.token || !sessionId) {
        return;
      }

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
        if (!resp.ok) {
          throw new Error(await readError(resp));
        }
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
        <div className="sidebar-top">
          <div className="brand">
            <div className="brand-mark">AI</div>
            <div>
              <div className="brand-title">智能体工作台</div>
              <div className="brand-subtitle">ReAct 问答服务</div>
            </div>
          </div>
          <button type="button" className="ghost-button strong" onClick={handleNewChat}>
            新建对话
          </button>
        </div>

        {isLoggedIn ? (
          <div className="user-strip">
            <span className="status-dot" />
            <span>已登录</span>
            <button onClick={logout} className="ghost-button" type="button">
              退出登录
            </button>
          </div>
        ) : (
          <form onSubmit={doLogin} className="login-card">
            <div className="panel-title">登录</div>
            <input
              type="text"
              placeholder="用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <input
              type="password"
              placeholder="密码"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="submit" className="primary-button">
              登录
            </button>
          </form>
        )}

        <div className="sidebar-section">
          <div className="panel-title">历史记录</div>
          <input
            className="search-input"
            type="text"
            placeholder="搜索会话"
            value={sessionSearch}
            onChange={(e) => setSessionSearch(e.target.value)}
          />
          <div className="session-list">
            {filteredSessions.length === 0 ? (
              <div className="empty-state compact">暂无已保存会话。</div>
            ) : (
              filteredSessions.map((session) => (
                <button
                  type="button"
                  key={session.id}
                  className={`session-card${session.id === sessionId ? " is-active" : ""}`}
                  onClick={() => handleSessionSelect(session)}
                >
                  <div className="session-card-top">
                    <span className="session-title">{session.title}</span>
                    <span className="session-time">{formatTime(session.updatedAt)}</span>
                  </div>
                  <div className="session-preview">
                    {session.turns[session.turns.length - 1]?.reasoningSummary ?? "暂无详情"}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        <div className="sidebar-section upload-panel">
          <div className="panel-title">知识库</div>
          <div
            className={`upload-dropzone${dragOver ? " is-over" : ""}${uploading ? " is-busy" : ""}`}
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
            <input
              ref={fileInputRef}
              type="file"
              className="upload-input"
              accept=".txt,.md,.markdown,.csv,.json,.log,.pdf,.docx"
              onChange={handleFileInput}
            />
            <div className="upload-copy">
              <div className="upload-title">{uploading ? "上传中..." : "拖拽文件到这里建立索引"}</div>
              <div className="upload-subtitle">文档会被加入当前检索工作区。</div>
            </div>
            <button
              type="button"
              className="ghost-button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              选择文件
            </button>
          </div>
          {uploadHint ? <div className="upload-hint">{uploadHint}</div> : null}
        </div>
      </aside>

      <main className="main-stage">
        <header className="topbar">
          <div>
            <div className="eyebrow">智能体对话</div>
            <h1>在一条时间线上展示思考、执行与回答</h1>
          </div>
          <div className="topbar-controls">
            <label className="select-wrap">
              <span>检索数</span>
              <input
                type="number"
                min={1}
                max={20}
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value) || 4)}
              />
            </label>
            {sessionId ? <div className="session-badge">会话 {sessionId.slice(0, 8)}</div> : null}
          </div>
        </header>

        <div className="workspace-grid">
          <section className="conversation-panel">
            <div className="conversation-scroll" ref={conversationRef}>
              {turns.length === 0 ? (
                <div className="empty-state large">
                  <div className="empty-title">提出一个有依据的问题</div>
                  <div className="empty-copy">
                    每一轮对话都会展示思考状态、工具执行、推理细节和最终回答。
                  </div>
                </div>
              ) : (
                turns.map((turn) => {
                  const isExpanded = Boolean(expandedReasoning[turn.id]);
                  const answerText =
                    turn.id === streamingTurnId ? turn.answer.slice(0, streamedChars) : turn.answer;
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
                            <div className="message-label">智能体</div>
                            <div className="agent-meta">{formatTime(turn.createdAt)}</div>
                          </div>
                          <div className="stage-rail">
                            <div className={`stage-pill${stage === "thinking" ? " is-active" : ""}`}>思考中</div>
                            <div className={`stage-pill${stage === "acting" ? " is-active" : ""}`}>执行中</div>
                            <div className={`stage-pill${stage === "response" ? " is-active" : ""}`}>已回答</div>
                          </div>
                        </div>

                        <div className="reasoning-bar">
                          <div>
                            <div className="reasoning-title">推理摘要</div>
                            <div className="reasoning-copy">{turn.reasoningSummary}</div>
                          </div>
                          <button type="button" className="ghost-button" onClick={() => toggleReasoning(turn.id)}>
                            {isExpanded ? "收起链路" : "查看链路"}
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

                        <div className="tool-list">
                          {turn.toolCalls.map((tool) => (
                            <div key={tool.id} className={`tool-card status-${tool.status}`}>
                              <div className="tool-header">
                                <div>
                                  <div className="tool-name">{tool.name}</div>
                                  <div className="tool-description">{tool.description}</div>
                                </div>
                                <div className={`tool-status is-${tool.status}`}>{getToolStatusLabel(tool.status)}</div>
                              </div>
                              <div className="tool-grid">
                                <div className="tool-block">
                                  <div className="tool-block-label">{tool.inputLabel}</div>
                                  <div className="tool-block-body">{tool.input}</div>
                                </div>
                                <div className="tool-block">
                                  <div className="tool-block-label">{tool.outputLabel}</div>
                                  <div className="tool-block-body pre-wrap">{tool.output}</div>
                                </div>
                              </div>
                              <div className="tool-meta">
                                {tool.meta.map((item) => (
                                  <span key={`${tool.id}-${item}`} className="meta-chip">
                                    {item}
                                  </span>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>

                        <div className="response-card">
                          <div className="response-header">
                            <div className="response-title">最终回答</div>
                            {turn.isPending ? <div className="typing-indicator">流式输出中</div> : null}
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

            <form onSubmit={sendQuestion} className="composer">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="输入你的问题，或基于已上传文档继续追问。"
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
                  {loading ? "执行中..." : "发送"}
                </button>
              </div>
            </form>
          </section>

          <aside className="inspector-panel">
            <div className="panel-title">检查面板</div>
            {selectedTurn ? (
              <>
                <div className="inspector-block">
                  <div className="inspector-label">问题</div>
                  <div className="inspector-value">{selectedTurn.userPrompt}</div>
                </div>
                <div className="inspector-block">
                  <div className="inspector-label">状态</div>
                  <div className="inspector-value">{selectedTurn.isPending ? "进行中" : "已完成"}</div>
                </div>
                <div className="inspector-block">
                  <div className="inspector-label">任务类型</div>
                  <div className="inspector-value">{selectedTurn.taskType === "summary" ? "Summary" : "QA"}</div>
                </div>
                {selectedTurn.retrievalSummary ? (
                  <div className="inspector-block">
                    <div className="inspector-label">检索摘要</div>
                    <div className="inspector-value">{selectedTurn.retrievalSummary}</div>
                  </div>
                ) : null}
                {selectedTurn.rerankSummary ? (
                  <div className="inspector-block">
                    <div className="inspector-label">重排摘要</div>
                    <div className="inspector-value">{selectedTurn.rerankSummary}</div>
                  </div>
                ) : null}
                {selectedTurn.rewrittenQueries.length > 0 ? (
                  <div className="inspector-block">
                    <div className="inspector-label">改写查询</div>
                    <div className="inspector-list">
                      {selectedTurn.rewrittenQueries.map((query) => (
                        <div key={`${selectedTurn.id}-${query}`} className="inspector-list-item">
                          {query}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {selectedTurn.summaryPhase ? (
                  <div className="inspector-block">
                    <div className="inspector-label">Summary 阶段</div>
                    <div className="inspector-value">{selectedTurn.summaryPhase}</div>
                  </div>
                ) : null}
                <div className="inspector-block">
                  <div className="inspector-label">推理过程</div>
                  <div className="inspector-list">
                    {selectedTurn.reasoningSteps.map((step, index) => (
                      <div key={`${selectedTurn.id}-inspect-${index}`} className="inspector-list-item">
                        {index + 1}. {step}
                      </div>
                    ))}
                  </div>
                </div>
                <div className="inspector-block">
                  <div className="inspector-label">工具调用</div>
                  <div className="inspector-list">
                    {selectedTurn.toolCalls.map((tool) => (
                      <div key={`${selectedTurn.id}-${tool.id}`} className="inspector-list-item">
                        {tool.name} / {getToolStatusLabel(tool.status)}
                      </div>
                    ))}
                  </div>
                </div>
                <div className="inspector-block">
                  <div className="inspector-label">引用来源</div>
                  <div className="inspector-list">
                    {selectedTurn.citations.length > 0 ? (
                      selectedTurn.citations.map((citation) => (
                        <div key={`${selectedTurn.id}-source-${citation.doc_id}`} className="inspector-list-item">
                          {citation.doc_id} ({citation.score.toFixed(2)})
                        </div>
                      ))
                    ) : (
                      <div className="inspector-list-item muted">当前没有附带引用。</div>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="empty-state compact">选择任意一轮对话，查看推理和工具详情。</div>
            )}
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
