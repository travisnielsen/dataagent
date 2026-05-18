# Contract — Chat SSE Stream (backend ↔ frontend)

**Endpoint**: `GET /api/chat/stream`
**Media type**: `text/event-stream`
**Status**: **Unchanged** by this feature.

## Why this contract is in the artifact set despite being unchanged

The previous (rescoped-away) version of this feature proposed renaming/repurposing this field. The current scope explicitly does **not** change anything here, and recording that as a frozen contract prevents future "while we're at it" drift from reintroducing the change.

## Request (query params)

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `message` | string | yes | User input. |
| `conversation_id` | string | no | Opaque session handle previously returned by the server. **Unchanged shape**: a server-managed Foundry Responses-protocol conversation id (the same value the Foundry portal Traces tab shows in the "Conversation ID" column). Frontend treats it as opaque. |
| `title` | string | no | Reserved; unused. |
| `request_id` | string | no | Clarification continuation id (unchanged). |

## Response (SSE events)

The router emits a sequence of `data: <json>\n\n` events. The shapes below are the *guaranteed* keys; other keys (`step`, `tool_call`, `text`, etc.) are unchanged from current behavior.

### `done` event (terminal)

```json
{
  "done": true,
  "conversation_id": "conv_..."
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `done` | `true` | yes | Stream terminator. |
| `conversation_id` | string \| null | yes when known | The server-managed Foundry conversation id for the session. Persisted by the frontend and sent back on the next turn. Unchanged shape vs. pre-upgrade. |

### `tool_call` event (mid-stream)

Unchanged. Continues to include `conversation_id` so the client can update its in-flight session handle.

### `error` event

Unchanged. Sanitized payload with a correlation id; never includes a raw provider error.

## Invariants

1. The server MUST return a non-null `conversation_id` on `done` for any successful turn.
2. The value MUST be the server-managed Foundry conversation id (Responses protocol). It is opaque to the client; the client MUST NOT parse or constrain its shape.
3. When the client supplies a `conversation_id` that the provider rejects (e.g., expired, wrong project), the server MUST respond with a sanitized error AND, on the *next* turn submitted with no `conversation_id`, MUST issue a fresh conversation transparently.
4. The Cadence backend MUST NOT construct or forward a `previous_response_id` value to the provider; the server-managed `conversation_id` is the sole continuity mechanism. (The Responses protocol itself may use `previous_response_id` server-side; this invariant scopes only Cadence's own code.)

## Backwards compatibility

The frontend [chatApi.ts](src/frontend/lib/chatApi.ts) and [useChatApi.ts](src/frontend/hooks/useChatApi.ts) require no change. They store and round-trip `conversation_id` as an opaque string.
