# opencode Architecture Deep Dive

## 1. Request Flow (How a message travels)

### Entry Point: `SessionPrompt.prompt()` (prompt.ts:1052)
- Creates user message via `createUserMessage()`
- Resolves `@file` references, images, MCP resources
- Calls `loop()` → `runLoop()`

### Run Loop: `runLoop()` (prompt.ts:1081)
- **The main agentic loop** — runs until no more tool calls
- Loads compacted messages (line 1092)
- Checks if last assistant message finished without tool calls → EXIT
- Resolves model, tools, system prompt
- Calls `processor.process()` → LLM stream
- If tool call → execute tool → loop again

### Processor: `processor.ts`
- Manages single LLM stream lifecycle
- Routes stream events: `text-delta`, `tool-call`, `tool-result`
- Creates `ToolPart` for each tool call
- Tracks step count, usage, cost
- Handles doom loop detection (3 identical calls)

### LLM Service: `llm.ts`
- Resolves provider → auth → language model
- **Runtime selection**: AI SDK (default) vs Native (experimental)
- AI SDK path: `streamText()` → `fullStream` → `toLLMEvents()`
- Native path: `LLMClient.stream()` → `LLMEvent` stream

---

## 2. Tool Call Lifecycle

### Registration (tools.ts:41)
- Iterates `registry.tools()` → wraps each as AI SDK `Tool`
- Each tool gets execution wrapper with plugin hooks
- MCP tools added from connected servers

### Execution Flow
```
1. LLM returns tool-call event
   └─ processor.ts:331-380 → ensureToolCall() → ToolPart(running)
   
2. Tool executes
   └─ tools.ts:102-131 → plugin.trigger("before") → execute() → plugin.trigger("after")
   
3. Result returned
   └─ processor.ts:383-413 → completeToolCall() → ToolPart(completed)
   
4. Loop continues
   └─ prompt.ts:1088-1329 → next LLM call with tool result in context
```

### Doom Loop Detection (processor.ts:356-380)
- Tracks last 3 tool calls
- If identical → break loop, return error

---

## 3. Streaming / SSE

### Request (llm.ts:280)
- `streamText()` called with prepared tools, messages, params
- Returns `result.fullStream` (AsyncIterable)

### AI SDK → LLMEvent (ai-sdk.ts:76)
- `toLLMEvents()` maps each AI SDK event to `LLMEvent`
- Handles: `text-delta`, `tool-call`, `tool-result`, `error`
- Tracks text/reasoning IDs for deduplication

### Transport (http.ts:81)
- `httpJson` transport sends POST request
- Framing: SSE (`Framing.sse`)
- `prepared.framing.frame(response.stream)` → parse SSE

---

## 4. Session Management

### Database (session.ts)
- SQLite-backed via Drizzle ORM
- Messages stored with parts (text, reasoning, tool-invocation)
- `updatePartDelta()` for streaming deltas

### State Machine
```
idle → busy (when LLM loop starts)
busy → idle (when loop exits)
busy → retry (on retryable error)
```

### Event System (EventV2Bridge)
- Publishes message/part updates
- Subscribers get real-time updates
- Used by TUI, web UI, API

---

## 5. Where Latency Comes From

### Network Latency (1-30s)
- **The biggest contributor** — LLM HTTP request
- DeepSeek web API: additional PoW solving overhead
- Our proxy: extra hop (opencode → proxy → browser → deepseek)

### Tool Execution (10ms-60s)
- Bash commands: depends on command
- File operations: fast (1-10ms)
- MCP resources: network dependent

### Overhead (100-500ms)
- Provider SDK import (first call only)
- Dynamic import of provider packages
- Snapshot tracking (filesystem state)

### Retry (2-30s)
- Exponential backoff on failure
- Rate limit handling
- 5xx error recovery

---

## 6. Our Current Architecture vs Optimal

### Current: opencode → Proxy → Browser → DeepSeek
```
1. opencode sends request to localhost:5051
2. Proxy builds context with tool instructions
3. Proxy calls DeepSeekClient.chat()
4. Client opens browser page → solves PoW → sends request
5. DeepSeek returns response (text + tool call)
6. Proxy extracts tool calls (XML/JSON parsing)
7. Proxy returns SSE stream with tool_calls format
8. opencode executes tool locally
9. opencode sends tool result back to proxy
10. Proxy forwards to model again
11. Model responds with final answer
```

### Latency Points in Our Setup:
1. **Browser PoW solving**: 2-10s per request
2. **Context building**: ~50ms (tool instruction injection)
3. **Tool call extraction**: ~10ms (regex parsing)
4. **SSE formatting**: ~5ms (streaming)
5. **Network round trip**: 1-5s (depending on response length)

### What We Can Improve:
1. **Pre-solve PoW**: Cache solved challenges for reuse
2. **Parallel tool calls**: Support multiple tool calls in one response
3. **Reduce context size**: Smaller prompts = faster response
4. **Connection pooling**: Reuse browser sessions
5. **Response caching**: Cache common responses

---

## 7. AI SDK Tool Call Format (What opencode Expects)

### Streaming Format
```
data: {"choices":[{"delta":{"role":"assistant","tool_calls":[{"id":"call_xxx","type":"function","function":{"name":"bash","arguments":""}}]}}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{...}"}}]}}]}
data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]
```

### Non-Streaming Format
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "tool_calls": [{
        "id": "call_xxx",
        "type": "function",
        "function": {
          "name": "bash",
          "arguments": "{\"command\": \"ls\"}"
        }
      }]
    },
    "finish_reason": "stop"
  }]
}
```

### Critical Requirements:
- `finish_reason: "tool_calls"` (not "stop")
- `tool_calls[].id` must be unique
- `tool_calls[].function.arguments` is a JSON string
- Streaming: deltas with `index` field for accumulation
