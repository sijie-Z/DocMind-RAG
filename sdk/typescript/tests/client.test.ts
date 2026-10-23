/** TS SDK 契约测试（node:test + 注入 fetch，不连真实服务）。 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { Client, DocMindError, SessionEvent } from "../src/index.js";

const SSE_BODY = [
  "event: chunk",
  'data: {"type":"chunk","content":"你好","conversationId":"s1"}',
  "",
  "id: s1:2",
  "event: message",
  'data: {"type":"message","content":"你好","conversationId":"s1"}',
  "",
  "",
].join("\n");

interface Call {
  url: string;
  init: RequestInit;
}

function stubFetch(handler: (url: string, init: RequestInit) => Response): {
  fetchImpl: typeof fetch;
  calls: Call[];
} {
  const calls: Call[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    const actualInit = init ?? {};
    calls.push({ url, init: actualInit });
    return handler(url, actualInit);
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function makeClient(handler: (url: string, init: RequestInit) => Response, options: Partial<{ maxRetries: number; retryBackoffMs: number; fetchImpl: typeof fetch }> = {}) {
  const stub = stubFetch(handler);
  const client = new Client({
    baseUrl: "http://testserver/",
    apiKey: "dm_test_key",
    fetchImpl: options.fetchImpl ?? stub.fetchImpl,
    maxRetries: options.maxRetries ?? 1,
    retryBackoffMs: options.retryBackoffMs ?? 1,
  });
  return { client, calls: stub.calls };
}

test("sessions.create sends auth and returns Session", async () => {
  const { client, calls } = makeClient(() =>
    new Response(JSON.stringify({ success: true, data: { id: "s1", title: "demo" } }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const session = await client.sessions.create("demo");
  assert.equal(session.id, "s1");
  assert.equal(session.title, "demo");
  assert.equal(calls[0].url, "http://testserver/api/v1/agent/sessions");
  const headers = calls[0].init.headers as Record<string, string>;
  assert.equal(headers.Authorization, "Bearer dm_test_key");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { title: "demo" });
});

test("chat.stream yields chunks and normalizes done", async () => {
  const { client, calls } = makeClient(
    () =>
      new Response(SSE_BODY, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
  );
  const events: SessionEvent[] = [];
  for await (const event of client.chat.stream("s1", "你好", "msg-1", {
    lastEventId: "s1:1",
  })) {
    events.push(event);
  }
  assert.deepEqual(
    events.map((event) => event.type),
    ["chunk", "message", "done"],
  );
  assert.equal(events[0].data.content, "你好");
  assert.equal(events[1].id, "s1:2");
  assert.equal(events[2].type, "done");

  const body = JSON.parse(String(calls[0].init.body));
  assert.equal(body.clientMessageId, "msg-1");
  assert.equal(body.conversationId, "s1");
  assert.equal(body.useAgent, true);
  const headers = calls[0].init.headers as Record<string, string>;
  assert.equal(headers["Last-Event-ID"], "s1:1");
});

test("AbortSignal is forwarded to fetch", async () => {
  const { client, calls } = makeClient(
    () =>
      new Response(SSE_BODY, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
  );
  const controller = new AbortController();
  for await (const _event of client.chat.stream("s1", "hi", "m", {
    signal: controller.signal,
  })) {
    // drain
  }
  assert.equal(calls[0].init.signal, controller.signal);
});

test("non-stream requests retry on 503 then succeed", async () => {
  let attempts = 0;
  const { client, calls } = makeClient(
    () => {
      attempts += 1;
      if (attempts < 3) {
        return new Response(JSON.stringify({ message: "busy" }), { status: 503 });
      }
      return new Response(JSON.stringify({ success: true, data: { sessions: [] } }), {
        status: 200,
      });
    },
    { maxRetries: 3, retryBackoffMs: 1 },
  );
  const sessions = await client.sessions.list();
  assert.deepEqual(sessions, []);
  assert.equal(calls.length, 3);
});

test("stream is never retried", async () => {
  let attempts = 0;
  const { client } = makeClient(() => {
    attempts += 1;
    return new Response(JSON.stringify({ message: "busy" }), { status: 503 });
  });
  await assert.rejects(
    async () => {
      for await (const _event of client.chat.stream("s1", "hi", "m")) {
        // drain
      }
    },
    (error: unknown) => error instanceof DocMindError && error.status === 503,
  );
  assert.equal(attempts, 1);
});

test("error mapping uses error_code and status", async () => {
  const { client } = makeClient(
    () =>
      new Response(
        JSON.stringify({
          success: false,
          error_code: "AUTHENTICATION_ERROR",
          message: "认证失败",
        }),
        { status: 401 },
      ),
  );
  await assert.rejects(
    async () => client.sessions.list(),
    (error: unknown) =>
      error instanceof DocMindError &&
      error.code === "AUTHENTICATION_ERROR" &&
      error.status === 401 &&
      error.message === "认证失败",
  );
});

test("fork, tools and knowledge wire to expected endpoints", async () => {
  const seen: string[] = [];
  const { client } = makeClient((url) => {
    seen.push(url);
    if (url.endsWith("/fork")) {
      return new Response(JSON.stringify({ success: true, data: { id: "child", title: "fork" } }), {
        status: 200,
      });
    }
    if (url.endsWith("/tools")) {
      return new Response(
        JSON.stringify({ success: true, data: [{ name: "search_knowledge_base" }] }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ query: "q", results: [], total_count: 0 }), {
      status: 200,
    });
  });
  const forked = await client.sessions.fork("s1", {
    messageId: "m1",
    includeCheckpoint: true,
  });
  assert.equal(forked.id, "child");
  const tools = await client.tools.list();
  assert.equal(tools[0].name, "search_knowledge_base");
  const result = await client.knowledge.search("q", { topK: 3, searchMode: "hybrid" });
  assert.equal(result.total_count, 0);
  assert.deepEqual(seen.map((url) => new URL(url).pathname), [
    "/api/v1/sessions/s1/fork",
    "/api/v1/agent/tools",
    "/api/v1/knowledge/search",
  ]);
});
