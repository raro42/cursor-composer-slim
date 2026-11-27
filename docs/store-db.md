# What is inside Composer `store.db`

## Short answer

Most bytes are the **agent session tape** (tool I/O + execution graph). A small slice is the human-readable dialogue.

## Measured mix (typical heavy chat)

| Kind | Share of bytes | Keep for training / history? |
| --- | --- | --- |
| `textish` / `binary` | ~55–60% | No — protobuf-ish calls, thoughts, IDs |
| `json_tool` | ~30–42% | No — full Shell/Read/Grep results |
| `json_user` + `json_assistant` | **~1–4%** | **Yes** |
| `json_system` | ≪1% | Usually product boilerplate |

Example: one ~381 MB chat held ~4 MB of user/assistant/system JSON (~1.1%).

## Does Cursor need the ~95%?

- **Yes while the chat is live / resumable.** The model must see prior tool output.
- **No forever for “chat history” as you read it.** Prefer `agent-transcripts/*.jsonl` or this tool’s export.

## Why so unstructured?

Cursor uses an **append-only blob store** (conversation DAG) for agent fidelity, not a small messages-only table with TTL. Forum posts from Cursor staff describe unbounded agent/chat DB growth and incomplete GC. Slimming locally is reasonable until product retention improves.

## Open tip

Read with SQLite `immutable=1` so a stale `-wal` lock does not block export.
