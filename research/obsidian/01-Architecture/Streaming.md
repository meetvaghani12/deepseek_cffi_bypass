# Streaming

## Overview
How opencode handles SSE streaming for real-time responses.

## Streaming Format

### OpenAI Format (What We Return)
```json
data: {"choices":[{"delta":{"role":"assistant","tool_calls":[{"id":"call_xxx","type":"function","function":{"name":"bash","arguments":""}}]}}]}
data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{...}"}}]}}]}
data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]
```

### Key Fields
- `delta.role`: "assistant" for first chunk
- `delta.tool_calls`: Array of tool calls
- `delta.tool_calls[].id`: Unique call ID
- `delta.tool_calls[].function.name`: Tool name
- `delta.tool_calls[].function.arguments`: JSON string
- `finish_reason`: "tool_calls" or "stop"

## Key Files

### HTTP Transport
- **File**: `packages/llm/src/route/transport/http.ts`
- **Function**: `httpJson()` (line 81)
- **Purpose**: Sends POST request, returns SSE stream

### SSE Framing
- **File**: `packages/llm/src/route/framing.ts`
- **Function**: `Framing.sse`
- **Purpose**: Parses SSE frames

### AI SDK Events
- **File**: `packages/opencode/src/session/llm/ai-sdk.ts`
- **Function**: `toLLMEvents()` (line 76)
- **Purpose**: Maps AI SDK events to LLMEvents

## Event Types

### Text Events
```typescript
text-start → textDelta → textEnd
```

### Tool Events
```typescript
tool-input-start → tool-input-delta → tool-input-end → tool-call
```

### Step Events
```typescript
start-step → finish-step
```

### Terminal Events
```typescript
finish (with reason: "stop" | "tool_calls" | "error")
```

## Streaming Flow

### 1. Request (llm.ts:280)
```typescript
const result = await streamText({
  model: language,
  messages: messages,
  tools: tools,
})

return result.fullStream
```

### 2. Event Mapping (ai-sdk.ts:76-286)
```typescript
switch (event.type) {
  case "text-delta":
    return [LLMEvent.textDelta({ id, text })]
  case "tool-call":
    return [LLMEvent.toolCall({ id, name, input })]
  case "finish":
    return [LLMEvent.finish({ reason, usage })]
}
```

### 3. Processor Handling (processor.ts:278-537)
```typescript
case "text-delta":
  await updateTextPart(event)
case "tool-call":
  await ensureToolCall(event)
case "finish":
  await calculateUsage(event)
```

## Our Proxy Streaming

### Implementation (proxy_server.py)
```python
def stream_tool_call(chat_id, created, model, tool_name, tool_args, call_id):
    # First chunk: role + tool call start
    yield make_sse_chunk(chat_id, created, model, {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": ""},
        }],
    })
    
    # Argument chunks
    args_str = json.dumps(tool_args)
    for i in range(0, len(args_str), 50):
        yield make_sse_chunk(chat_id, created, model, {
            "tool_calls": [{
                "index": 0,
                "function": {"arguments": args_str[i:i+50]},
            }],
        })
    
    # Finish
    yield make_sse_chunk(chat_id, created, model, {}, finish_reason="tool_calls")
    yield "data: [DONE]\n\n"
```

### Key Requirements
1. **First chunk**: Must include `role: "assistant"` and `tool_calls` with empty arguments
2. **Argument chunks**: Must include `index: 0` for accumulation
3. **Finish chunk**: Must include `finish_reason: "tool_calls"`
4. **Terminal**: Must end with `data: [DONE]`

## Latency Considerations

### Streaming Overhead
- **Chunk size**: Smaller = more chunks = more overhead
- **Network**: TCP buffering affects perceived latency
- **Client**: AI SDK accumulates deltas

### Optimization
1. **Larger chunks**: Reduce number of SSE frames
2. **Direct streaming**: Skip proxy buffering
3. **Compression**: gzip SSE responses

## Error Handling

### Stream Errors
- **Network error**: Retry with backoff
- **Parse error**: Return error event
- **Timeout**: Cancel stream, return error

### Recovery
- **Partial response**: Continue from last chunk
- **Tool call error**: Return tool-error event
- **Model error**: Return provider-error event

## Key Insights

1. **SSE is efficient**: Minimal overhead for real-time
2. **Format is strict**: AI SDK expects exact format
3. **Tool calls are special**: Need `finish_reason: "tool_calls"`
4. **Streaming is stateful**: Must track accumulation

## Related Notes

- [[Request Flow]]
- [[Tool System]]
- [[Latency Analysis]]
- [[Improvement Opportunities]]

---

**Tags**: #streaming #sse #real-time #opencode
**Last Updated**: 2026-07-13
