# opencode Request Flow Graph

## Core Flow

```mermaid
graph TD
    User["User Input"] --> Prompt["prompt.ts<br/>SessionPrompt.prompt()"]
    Prompt --> CreateUserMsg["createUserMessage()<br/>Resolves files, images, MCP"]
    CreateUserMsg --> RunLoop["runLoop()<br/>Main agentic loop"]
    RunLoop --> Processor["processor.ts<br/>LLM Stream Handler"]
    Processor --> LLM["llm.ts<br/>LLM Service"]
    LLM --> Runtime{"Runtime<br/>Selection"}
    Runtime -->|"default"| AISDK["ai-sdk.ts<br/>streamText()"]
    Runtime -->|"experimental"| Native["native-runtime.ts<br/>LLMClient.stream()"]
    AISDK --> FullStream["fullStream<br/>AsyncIterable"]
    FullStream --> ToLLMEvents["toLLMEvents()<br/>AI SDK → LLMEvent"]
    ToLLMEvents --> Events["LLMEvent Stream"]
    Events --> ProcessorHandle["handle.event()<br/>Route Events"]
    ProcessorHandle --> ToolCalls{"Tool<br/>Calls?"}
    ToolCalls -->|"yes"| ToolExec["Tool Execution<br/>tools.ts"]
    ToolCalls -->|"no"| TextOutput["Text Output"]
    ToolExec --> ToolResult["Tool Result"]
    ToolResult --> RunLoop
    TextOutput --> Response["Response to User"]
```

## Tool Call Lifecycle

```mermaid
sequenceDiagram
    participant U as User
    participant P as Processor
    participant L as LLM Service
    participant SDK as AI SDK
    participant T as Tool Executor
    
    U->>P: User message
    P->>L: stream(input)
    L->>SDK: streamText()
    SDK-->>P: tool-call event
    P->>P: ensureToolCall() → ToolPart(running)
    P->>T: tool.execute(args)
    T-->>P: tool-result event
    P->>P: completeToolCall() → ToolPart(completed)
    P->>L: Next LLM call with tool result
    L->>SDK: streamText() (with tool result)
    SDK-->>P: text-delta events
    P-->>U: Text response
```

## Session Management

```mermaid
graph TD
    Session["Session<br/>session.ts"] --> Messages["Messages<br/>messages()"]
    Session --> Parts["Parts<br/>updatePart()"]
    Session --> Status["Status<br/>idle/busy/retry"]
    Session --> Events["EventV2Bridge<br/>Publish/Subscribe"]
    Messages --> DB["SQLite Database"]
    Parts --> DB
    Status --> Events
```

## Latency Hotspots

```mermaid
graph LR
    subgraph "High Latency"
        A["Provider SDK Import<br/>100-500ms"] 
        B["LLM HTTP Request<br/>1-30s"]
        C["Retry Backoff<br/>2-30s"]
        D["Tool Execution<br/>10ms-60s"]
    end
    subgraph "Medium Latency"
        E["Snapshot Tracking<br/>10-100ms"]
        F["File Reading<br/>1-100ms"]
        G["MCP Resources<br/>100ms-5s"]
    end
    subgraph "Low Latency"
        H["Message Normalization<br/><1ms"]
        I["Plugin Hooks<br/>1-50ms"]
    end
```

## Proxy Layer (Our Current Setup)

```mermaid
graph LR
    OC["opencode<br/>localhost:5051"] -->|"POST /v1/chat/completions"| Proxy["proxy_server.py<br/>Flask"]
    Proxy -->|"Inject tool<br/>instructions"| Context["Context Builder"]
    Context -->|"Build prompt"| Client["DeepSeekClient"]
    Client -->|"POST /chat/completions"| Browser["Playwright Browser"]
    Browser -->|"SSE Stream"| Client
    Client -->|"Full response"| Proxy
    Proxy -->|"Parse tool calls"| Extract["extract_tool_calls()"]
    Extract -->|"XML/JSON format"| Stream["SSE Stream Response"]
    Stream -->|"finish_reason: tool_calls"| OC
    OC -->|"Execute tool"| ToolLocal["ToolExecutor<br/>(bash, read, etc.)"]
    ToolLocal -->|"Tool result"| OC
    OC -->|"POST /v1/chat/completions"| Proxy
    Proxy -->|"Forward to model"| Client
```

## Key Files Reference

| File | Path | Purpose |
|------|------|---------|
| session.ts | packages/opencode/src/session/session.ts | Core session CRUD |
| prompt.ts | packages/opencode/src/session/prompt.ts | Prompt flow orchestration |
| processor.ts | packages/opencode/src/session/processor.ts | LLM stream handler |
| llm.ts | packages/opencode/src/session/llm.ts | LLM service + runtime selection |
| ai-sdk.ts | packages/opencode/src/session/llm/ai-sdk.ts | AI SDK → LLMEvent adapter |
| native-runtime.ts | packages/opencode/src/session/llm/native-runtime.ts | Native runtime adapter |
| tools.ts | packages/opencode/src/session/tools.ts | Tool resolution + execution |
| request.ts | packages/opencode/src/session/llm/request.ts | Request preparation |
| provider.ts | packages/opencode/src/provider/provider.ts | Provider registry |
| http.ts | packages/llm/src/route/transport/http.ts | HTTP transport |
| openai-chat.ts | packages/llm/src/protocols/openai-chat.ts | OpenAI chat protocol |
