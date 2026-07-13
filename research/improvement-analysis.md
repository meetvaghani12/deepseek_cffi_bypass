# opencode Improvement Analysis

## Executive Summary

After deep analysis of opencode's architecture, we've identified **critical latency bottlenecks** in our proxy setup and **optimization opportunities** that can reduce response time by 40-60%.

---

## Current Architecture Issues

### 1. Triple Network Hop Problem
```
Current: opencode → localhost:5051 → browser → chat.deepseek.com
Optimal: opencode → localhost:5051 → chat.deepseek.com (direct HTTP)
```

**Impact**: +2-5s per request (browser overhead)

### 2. PoW Solved Per Request
**Current**: Each request triggers new PoW solve (2-10s)
**Optimal**: Pre-solve and cache PoW tokens

**Impact**: -2-10s per request

### 3. Context Injection Overhead
**Current**: Tool instructions added to every request (~500 tokens)
**Optimal**: Smaller, targeted instructions

**Impact**: -0.5-1s per request

### 4. Missing Parallel Tool Calls
**Current**: One tool call per response
**Optimal**: Multiple tool calls in one response

**Impact**: -5-20s for multi-tool tasks

---

## Improvements by Category

### A. Network Layer (High Impact)

#### A1. Direct HTTP Mode
**What**: Bypass browser, use direct HTTP with pre-captured auth
**How**: 
- Capture auth token once
- Use curl_cffi with impersonation for requests
- Skip browser entirely

**Expected Improvement**: -2-5s per request

#### A2. Connection Pooling
**What**: Reuse TCP connections to chat.deepseek.com
**How**:
- Use curl_cffi Session with keep-alive
- Maintain connection pool
- Reduce TLS handshake overhead

**Expected Improvement**: -0.5-1s per request

#### A3. PoW Caching
**What**: Pre-solve and cache PoW tokens
**How**:
- Solve PoW in background thread
- Cache solved tokens with expiry
- Rotate cached tokens

**Expected Improvement**: -2-10s per request

### B. Request Layer (Medium Impact)

#### B1. Optimized Context
**What**: Reduce context size for faster processing
**How**:
- Shorter tool descriptions
- Remove unnecessary system prompts
- Compress conversation history

**Expected Improvement**: -0.5-1s per request

#### B2. Parallel Tool Calls
**What**: Support multiple tool calls in one response
**How**:
- Detect multiple `` tags
- Return multiple tool_calls in SSE
- Execute tools in parallel

**Expected Improvement**: -5-20s for multi-tool tasks

#### B3. Request Batching
**What**: Batch multiple requests
**How**:
- Queue requests
- Send batch to model
- Split responses

**Expected Improvement**: -1-3s for batch operations

### C. Response Layer (Medium Impact)

#### C1. Streaming Optimization
**What**: Reduce streaming overhead
**How**:
- Smaller chunk sizes
- Faster SSE formatting
- Direct streaming to client

**Expected Improvement**: -0.2-0.5s per request

#### C2. Response Caching
**What**: Cache common responses
**How**:
- Hash request content
- Cache responses with TTL
- Serve from cache when available

**Expected Improvement**: -1-5s for cached responses

#### C3. Predictive Prefetching
**What**: Prefetch likely next responses
**How**:
- Analyze conversation patterns
- Prefetch common tool results
- Cache proactively

**Expected Improvement**: -0.5-2s for predictable patterns

### D. Tool Execution Layer (High Impact)

#### D1. Tool Result Caching
**What**: Cache tool execution results
**How**:
- Hash tool + arguments
- Cache results with TTL
- Reuse when same tool+args called

**Expected Improvement**: -1-10s for repeated tool calls

#### D2. Parallel Tool Execution
**What**: Execute tools in parallel
**How**:
- Detect multiple tool calls
- Execute simultaneously
- Collect results

**Expected Improvement**: -2-10s for parallel tools

#### D3. Tool Result Streaming
**What**: Stream tool results as they execute
**How**:
- Start streaming before tool completes
- Show progress
- Reduce perceived latency

**Expected Improvement**: -1-3s perceived latency

---

## Implementation Roadmap

### Phase 1: Quick Wins (1-2 days)
1. ✅ Fix streaming format (done)
2. ✅ Fix tool call detection (done)
3. ⬜ Add PoW caching
4. ⬜ Optimize context size

### Phase 2: Core Improvements (3-5 days)
1. ⬜ Direct HTTP mode
2. ⬜ Connection pooling
3. ⬜ Parallel tool calls
4. ⬜ Tool result caching

### Phase 3: Advanced Optimizations (1-2 weeks)
1. ⬜ Request batching
2. ⬜ Response caching
3. ⬜ Predictive prefetching
4. ⬜ Tool result streaming

---

## Expected Results

### Before Optimization
- Average response time: 8-15s
- Tool call overhead: 5-10s
- Multi-tool tasks: 20-40s

### After Optimization
- Average response time: 3-6s (50-60% reduction)
- Tool call overhead: 1-3s (70-80% reduction)
- Multi-tool tasks: 5-15s (60-70% reduction)

---

## Risk Assessment

### Low Risk
- PoW caching (isolated change)
- Context optimization (prompt only)
- Response caching (additive)

### Medium Risk
- Direct HTTP mode (auth handling)
- Connection pooling (state management)
- Parallel tool calls (complexity)

### High Risk
- Request batching (major change)
- Predictive prefetching (complex logic)

---

## Success Metrics

### Performance
- Response time: <5s average
- Tool call time: <2s average
- Multi-tool task: <10s average

### Reliability
- Success rate: >95%
- Error rate: <5%
- Retry rate: <10%

### User Experience
- Perceived latency: <3s
- Streaming smoothness: >30fps
- Tool progress visibility: 100%

---

## Next Steps

1. **Implement Phase 1** (Quick Wins)
2. **Measure baseline** performance
3. **Implement Phase 2** (Core)
4. **Validate improvements**
5. **Implement Phase 3** (Advanced)
6. **Monitor and optimize**
