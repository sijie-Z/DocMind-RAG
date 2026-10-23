/**
 * DocMind TypeScript SDK（issue #480 §5.6）。
 *
 * - 与 Python SDK 同名接口：sessions.create/list/fork、chat.stream、
 *   approvals.respond、tools.list、knowledge.search；
 * - `stream()` 返回 AsyncIterable<SessionEvent>；取消用 AbortSignal；
 * - 非流式请求仅对 429/502/503/504 重试（指数退避 + 抖动，≤3 次）；
 *   流式请求不自动重试（避免重复消息）。
 */

export class DocMindError extends Error {
  code: string;
  status?: number;

  constructor(code: string, message: string, status?: number) {
    super(message);
    this.name = 'DocMindError';
    this.code = code;
    this.status = status;
  }
}

export interface Session {
  id: string;
  title: string;
  raw: Record<string, unknown>;
}

export interface SessionEvent {
  type: string;
  data: Record<string, unknown>;
  id?: string;
}

export interface StreamOptions {
  signal?: AbortSignal;
  lastEventId?: string;
}

export interface ClientOptions {
  baseUrl: string;
  apiKey: string;
  fetchImpl?: typeof fetch;
  maxRetries?: number;
  retryBackoffMs?: number;
}

const RETRY_STATUS = new Set([429, 502, 503, 504]);
const DEFAULT_MAX_RETRIES = 3;

/** 去掉末尾所有 `/`（线性扫描，避免正则回溯被 CodeQL 判为 ReDoS）。 */
function stripTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value.charCodeAt(end - 1) === 47 /* '/' */) end -= 1;
  return value.slice(0, end);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toSession(data: Record<string, unknown>): Session {
  return {
    id: String(data.id ?? ''),
    title: String(data.title ?? ''),
    raw: data,
  };
}

function parseFrame(text: string): SessionEvent | undefined {
  let event: string | undefined;
  let eventId: string | undefined;
  const dataLines: string[] = [];
  for (const rawLine of text.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('id:')) eventId = line.slice(3).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }
  if (!event && dataLines.length === 0) return undefined;
  const payloadText = dataLines.join('\n');
  let data: Record<string, unknown>;
  try {
    const parsed = payloadText ? JSON.parse(payloadText) : {};
    data =
      typeof parsed === 'object' && parsed !== null
        ? (parsed as Record<string, unknown>)
        : { value: parsed };
  } catch {
    data = { raw: payloadText };
  }
  return {
    type: event ?? String(data.type ?? 'message'),
    data,
    id: eventId || undefined,
  };
}

export class Client {
  readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxRetries: number;
  private readonly retryBackoffMs: number;

  readonly sessions = {
    create: async (title = 'New Agent Session'): Promise<Session> => {
      const data = await this.requestJson('POST', '/api/v1/agent/sessions', { title });
      return toSession((data ?? {}) as Record<string, unknown>);
    },
    list: async (page = 1, pageSize = 20): Promise<Session[]> => {
      const data = await this.requestJson(
        'GET',
        `/api/v1/agent/sessions?page=${page}&page_size=${pageSize}`,
      );
      const items = Array.isArray((data as { sessions?: unknown[] })?.sessions)
        ? ((data as { sessions: Record<string, unknown>[] }).sessions)
        : [];
      return items.map(toSession);
    },
    fork: async (
      sessionId: string,
      options: {
        messageId?: string;
        title?: string;
        includeCheckpoint?: boolean;
        seed?: string;
      } = {},
    ): Promise<Session> => {
      const body: Record<string, unknown> = {};
      if (options.messageId !== undefined) body.message_id = options.messageId;
      if (options.title !== undefined) body.title = options.title;
      if (options.includeCheckpoint !== undefined) {
        body.include_checkpoint = options.includeCheckpoint;
      }
      if (options.seed !== undefined) body.seed = options.seed;
      const data = await this.requestJson(
        'POST',
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/fork`,
        body,
      );
      const record = data as Record<string, unknown>;
      const session =
        record && typeof record.session === 'object' && record.session !== null
          ? (record.session as Record<string, unknown>)
          : record;
      return toSession(session ?? {});
    },
  };

  readonly chat = {
    stream: (
      sessionId: string,
      prompt: string,
      clientMessageId?: string,
      options: StreamOptions = {},
    ): AsyncIterable<SessionEvent> => this.streamEvents(sessionId, prompt, clientMessageId, options),
  };

  readonly approvals = {
    respond: async (
      sessionId: string,
      approvalId: string,
      decision: 'allow' | 'deny' | 'approve',
      remember = false,
    ): Promise<Record<string, unknown>> => {
      if (!['allow', 'deny', 'approve'].includes(decision)) {
        throw new DocMindError('VALIDATION_ERROR', 'decision must be allow or deny');
      }
      const httpDecision = decision === 'deny' ? 'deny' : 'approve';
      // 审批端点返回 SSE（续跑事件流），不是 JSON——消费到流结束取最后一个事件。
      let last: Record<string, unknown> = { ok: true };
      for await (const event of this.streamPost(
        `/api/v1/agent/tasks/${encodeURIComponent(approvalId)}/approval`,
        { decision: httpDecision, note: remember ? 'remember' : null },
        {},
      )) {
        last = event.data;
      }
      return last;
    },
  };

  readonly tools = {
    list: async (): Promise<Record<string, unknown>[]> => {
      const data = await this.requestJson('GET', '/api/v1/agent/tools');
      return Array.isArray(data) ? (data as Record<string, unknown>[]) : [];
    },
  };

  readonly knowledge = {
    search: async (
      query: string,
      options: { topK?: number; searchMode?: string } = {},
    ): Promise<Record<string, unknown>> => {
      const body: Record<string, unknown> = { query };
      if (options.topK !== undefined) body.top_k = options.topK;
      if (options.searchMode !== undefined) body.search_mode = options.searchMode;
      const data = await this.requestJson('POST', '/api/v1/knowledge/search', body);
      return (data ?? {}) as Record<string, unknown>;
    },
  };

  constructor(options: ClientOptions) {
    if (!options.apiKey) {
      throw new DocMindError('AUTHENTICATION_ERROR', 'apiKey is required', 401);
    }
    this.baseUrl = stripTrailingSlashes(options.baseUrl);
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.maxRetries = Math.max(1, options.maxRetries ?? DEFAULT_MAX_RETRIES);
    this.retryBackoffMs = options.retryBackoffMs ?? 500;
  }

  private headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      Accept: 'application/json',
      'Content-Type': 'application/json',
    };
  }

  private async requestJson(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<unknown> {
    let lastResponse: Response | undefined;
    for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers: this.headers(),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      });
      if (RETRY_STATUS.has(response.status) && attempt < this.maxRetries) {
        lastResponse = response;
        await sleep(this.retryDelay(attempt, response));
        continue;
      }
      return this.parsePayload(response);
    }
    return this.parsePayload(lastResponse as Response);
  }

  private retryDelay(attempt: number, response?: Response): number {
    const retryAfter = response?.headers.get('Retry-After');
    if (retryAfter) {
      const seconds = Number(retryAfter);
      if (Number.isFinite(seconds)) return seconds * 1000;
    }
    const jitter = 0.5 + Math.random();
    return this.retryBackoffMs * 2 ** (attempt - 1) * jitter;
  }

  private async errorFromResponse(response: Response): Promise<DocMindError> {
    let body: Record<string, unknown> = {};
    try {
      body = (await response.json()) as Record<string, unknown>;
    } catch {
      body = {};
    }
    const code = String(body.error_code ?? body.code ?? `HTTP_${response.status}`);
    const message = String(body.message ?? body.detail ?? code);
    return new DocMindError(code, message, response.status);
  }

  private async parsePayload(response: Response): Promise<unknown> {
    if (response.status >= 400) {
      throw await this.errorFromResponse(response);
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new DocMindError('INVALID_RESPONSE', 'server returned non-JSON body', response.status);
    }
    if (
      typeof body === 'object' &&
      body !== null &&
      (body as { success?: unknown }).success === true &&
      'data' in body
    ) {
      return (body as { data: unknown }).data;
    }
    return body;
  }

  private streamEvents(
    sessionId: string,
    prompt: string,
    clientMessageId: string | undefined,
    options: StreamOptions,
  ): AsyncGenerator<SessionEvent> {
    const body: Record<string, unknown> = {
      content: prompt,
      conversationId: sessionId,
      useAgent: true,
    };
    if (clientMessageId !== undefined) {
      body.payload = { clientMessageId };
    }
    return this.streamPost('/api/v1/chat/stream', body, options, {
      conversationId: sessionId,
    });
  }

  private async *streamPost(
    path: string,
    body: Record<string, unknown>,
    options: StreamOptions,
    doneFallback?: Record<string, unknown>,
  ): AsyncGenerator<SessionEvent> {
    const headers = this.headers();
    headers.Accept = 'text/event-stream';
    if (options.lastEventId) headers['Last-Event-ID'] = options.lastEventId;

    // 流式请求不重试：失败如实抛出（避免重复消息）。
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: options.signal,
    });
    if (response.status >= 400) {
      throw await this.errorFromResponse(response);
    }
    if (!response.body) {
      throw new DocMindError('INVALID_RESPONSE', 'response body missing', response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let sawDone = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const frameText = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseFrame(frameText);
        if (event) {
          if (event.type === 'done') sawDone = true;
          yield event;
        }
        boundary = buffer.indexOf('\n\n');
      }
    }
    buffer += decoder.decode();
    const tail = parseFrame(buffer);
    if (tail) {
      if (tail.type === 'done') sawDone = true;
      yield tail;
    }
    if (!sawDone) {
      if (doneFallback) {
        yield { type: 'done', data: doneFallback };
      }
    }
  }
}
