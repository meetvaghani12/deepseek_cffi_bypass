# opencode Research Vault

## Vault Structure

```
opencode-research/
├── 00-Inbox/
│   └── Quick Notes.md
├── 01-Architecture/
│   ├── Request Flow.md
│   ├── Tool System.md
│   ├── Streaming.md
│   └── Session Management.md
├── 02-Analysis/
│   ├── Latency Analysis.md
│   ├── Bottleneck Identification.md
│   └── Improvement Opportunities.md
├── 03-Improvements/
│   ├── Quick Wins.md
│   ├── Core Improvements.md
│   └── Advanced Optimizations.md
├── 04-Implementation/
│   ├── Phase 1 Plan.md
│   ├── Phase 2 Plan.md
│   └── Phase 3 Plan.md
├── 05-Research/
│   ├── Agent Swarm Workflow.md
│   ├── Code Analysis.md
│   └── Testing Strategy.md
├── Templates/
│   ├── Research Template.md
│   └── Analysis Template.md
└── Dashboard/
    └── Progress Tracker.md
```

---

## Core Notes

### [[Request Flow]]
How a message travels through opencode

### [[Tool System]]
Tool calling lifecycle and execution

### [[Streaming]]
SSE streaming format and handling

### [[Session Management]]
Session state and lifecycle

### [[Latency Analysis]]
Where time is spent in the pipeline

### [[Bottleneck Identification]]
Critical path bottlenecks

### [[Improvement Opportunities]]
Optimization opportunities

---

## Key Insights

### 1. The Triple Hop Problem
Our current setup has unnecessary network hops:
```
opencode → proxy → browser → DeepSeek
```
Should be:
```
opencode → proxy → DeepSeek (direct)
```

### 2. PoW is the Biggest Bottleneck
- Current: 2-10s per request
- Optimized: 0s (cached) or <1s (pre-solved)

### 3. Context Size Matters
- Current: ~500 tokens tool instructions
- Optimized: ~100 tokens targeted instructions

### 4. Parallel Tools = Big Win
- Current: 1 tool call per response
- Optimized: Multiple tool calls in one response

---

## Navigation

### Quick Start
1. Read [[Request Flow]] to understand the system
2. Read [[Latency Analysis]] to see where time is spent
3. Read [[Quick Wins]] for immediate improvements

### Deep Dive
1. [[Tool System]] - How tools work
2. [[Streaming]] - SSE format details
3. [[Session Management]] - State handling

### Implementation
1. [[Phase 1 Plan]] - Quick wins
2. [[Phase 2 Plan]] - Core improvements
3. [[Phase 3 Plan]] - Advanced optimizations

---

## Tags

- #architecture
- #latency
- #optimization
- #tool-calling
- #streaming
- #deepseek
- #proxy
- #performance

---

## Last Updated
2026-07-13
