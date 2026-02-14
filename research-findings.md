# WebSocket Reconnection Handling: Best Practices Research

**Date:** February 14, 2026  
**Project:** lobs-server  
**Context:** Real-time WebSocket messaging with OpenClaw agent bridge  

---

## Executive Summary

WebSocket connections are inherently unreliable and will disconnect due to network issues, server restarts, or client sleep modes. **Production applications must implement automatic reconnection** with exponential backoff to provide resilient real-time experiences.

**Key Findings:**
1. **Native WebSocket API does not include reconnection** - must be implemented at application level
2. **Exponential backoff with jitter** is the industry standard (prevents thundering herd)
3. **Connection state tracking** is critical for managing subscriptions and messages
4. **Popular libraries** (reconnecting-websocket, centrifuge-js) provide battle-tested patterns

**Recommended for lobs-server:**
- Implement **client-side** reconnection wrapper (server stays simple)
- Use exponential backoff: `min=1s, max=20s, factor=1.3, +jitter`
- Add **connection state indicators** in UI
- **Buffer messages** during disconnection for delivery on reconnect

---

## 1. WHY RECONNECTION IS ESSENTIAL

### 1.1 Common Disconnect Scenarios

**Network-related:**
- Mobile device switches WiFi ↔ cellular
- User enters tunnel, loses signal temporarily
- Corporate firewall closes idle connections
- Network proxy times out connection

**Server-related:**
- Server restart/deployment (planned maintenance)
- Load balancer failover
- Server crash/OOM

**Client-related:**
- Browser tab backgrounded (mobile Safari aggressive cleanup)
- Device sleep mode
- User navigates away and returns

**Statistics:**
- WebSocket connections drop **every 2-10 minutes** on mobile networks (source: centrifuge-js docs)
- Desktop browsers: ~1-5% of connections drop per hour

### 1.2 Current lobs-server Implementation

From `/Users/lobs/lobs-server/app/routers/chat.py`:

```python
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, ...):
    await manager.connect(websocket, session_key)
    
    try:
        while True:
            data = await websocket.receive_json()
            # Handle messages...
    
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_key)  # ✅ Clean disconnect
    except Exception as e:
        manager.disconnect(websocket, session_key)
        print(f"WebSocket error: {e}")
```

**Current behavior:**
- ✅ Server handles disconnect gracefully (cleanup)
- ❌ No client-side reconnection logic
- ❌ Messages sent during disconnect are lost
- ❌ User must manually refresh page to reconnect

---

## 2. EXPONENTIAL BACKOFF STRATEGIES

### 2.1 The Problem: Thundering Herd

When a server restarts, **all clients** attempt to reconnect simultaneously. If using fixed retry interval:

```
Server crashes at t=0
1000 clients all retry at t=5s → server overload → crashes again
1000 clients all retry at t=10s → server overload → crashes again
(infinite loop)
```

**Solution:** Exponential backoff + jitter

### 2.2 Industry Standard Pattern

**Formula:**
```javascript
delay = min(maxDelay, minDelay * (growthFactor ** attemptNumber)) + random(0, jitter)
```

**Typical values (from production libraries):**

| Library | Min Delay | Max Delay | Growth Factor | Jitter | Max Retries |
|---------|-----------|-----------|---------------|--------|-------------|
| **reconnecting-websocket** | 1s + rand(0-4s) | 10s | 1.3 | Built-in | ∞ |
| **centrifuge-js** | 500ms | 20s | 1.3 | Yes | ∞ |
| **Socket.io** | 1s | 5s | 2.0 | Yes | ∞ |

**Why these values:**
- **Min delay (500ms-1s):** Fast recovery for transient issues
- **Max delay (10-20s):** Prevents indefinite waiting, but not too aggressive
- **Growth factor (1.3-2.0):** 1.3 is gentler, 2.0 backs off faster
- **Jitter:** Random 0-25% prevents synchronized retries
- **Max retries (∞):** Network apps should always try to reconnect

### 2.3 Exponential Backoff Example

**Reconnection timeline with min=1s, max=20s, factor=1.3:**

| Attempt | Base Delay | With Jitter (±25%) | Cumulative Time |
|---------|------------|---------------------|-----------------|
| 1 | 1.0s | 0.8s - 1.2s | ~1s |
| 2 | 1.3s | 1.0s - 1.6s | ~2.5s |
| 3 | 1.7s | 1.3s - 2.1s | ~4.5s |
| 4 | 2.2s | 1.7s - 2.8s | ~7s |
| 5 | 2.9s | 2.2s - 3.6s | ~10s |
| 10 | 13.8s | 10.4s - 17.3s | ~70s |
| 15+ | 20s (max) | 15s - 25s | Ongoing |

**Observations:**
- First reconnect happens quickly (~1s) - good UX
- By attempt 10, backing off to max delay - prevents server overload
- Jitter spreads reconnections over time window

---

## 3. PRODUCTION PATTERNS & LIBRARIES

### 3.1 Reconnecting-WebSocket Library

**Source:** https://github.com/pladaria/reconnecting-websocket  
**Downloads:** 1.5M/week on npm  
**Battle-tested:** Used by major production apps  

**Key Features:**
```javascript
import ReconnectingWebSocket from 'reconnecting-websocket';

const options = {
    minReconnectionDelay: 1000,      // 1s min
    maxReconnectionDelay: 10000,     // 10s max
    reconnectionDelayGrowFactor: 1.3, // Exponential factor
    connectionTimeout: 4000,          // Timeout for connection attempt
    maxRetries: Infinity,             // Never give up
    maxEnqueuedMessages: Infinity,    // Buffer messages during disconnect
    debug: false                      // Debug logging
};

const rws = new ReconnectingWebSocket('ws://localhost:8000/ws', [], options);

// Drop-in replacement for WebSocket
rws.addEventListener('open', () => {
    console.log('Connected');
});

rws.addEventListener('message', (event) => {
    console.log('Message:', event.data);
});

// Automatically reconnects on disconnect!
```

**How it works:**
1. Wraps native WebSocket with reconnection logic
2. Maintains WebSocket-compatible API (drop-in replacement)
3. Queues messages sent during disconnect
4. Delivers queued messages on reconnect
5. Exposes `readyState` and `retryCount` for UI indicators

**Advantages:**
- ✅ Minimal code changes (wrapper pattern)
- ✅ Handles message buffering automatically
- ✅ Works in browser, React Native, Node.js
- ✅ No dependencies

**Disadvantages:**
- ❌ No built-in authentication token refresh
- ❌ No subscription state management (just transport layer)

### 3.2 Centrifuge-JS Pattern

**Source:** https://github.com/centrifugal/centrifuge-js  
**Used by:** Centrifugo real-time messaging server  

**Architecture:**
```javascript
import { Centrifuge } from 'centrifuge';

const centrifuge = new Centrifuge('ws://localhost:8000/connection/websocket', {
    minReconnectDelay: 500,           // 500ms min
    maxReconnectDelay: 20000,         // 20s max
    timeout: 5000,                    // Operation timeout
    debug: true                       // Debug logging
});

// Connection lifecycle events
centrifuge.on('connecting', (ctx) => {
    console.log('Connecting...', ctx.code, ctx.reason);
});

centrifuge.on('connected', (ctx) => {
    console.log('Connected');
});

centrifuge.on('disconnected', (ctx) => {
    console.log('Disconnected:', ctx.code, ctx.reason);
    // Will automatically reconnect
});

centrifuge.connect();
```

**Advanced features:**
- **Connection state machine:** connecting → connected → disconnected
- **Subscription management:** Separate connection vs. subscription states
- **Token refresh:** Built-in JWT token refresh mechanism
- **Multiple transports:** WebSocket, HTTP-streaming, SSE fallbacks
- **Backpressure handling:** `bufferedAmount` checks before sending

**Key insight from docs:**

> "WebSocket by itself does not include reconnection, authentication and many other high-level mechanisms. So there are client/server libraries for that."  
> — javascript.info/websocket

### 3.3 Connection State Tracking

**State machine (from centrifuge-js):**

```
                ┌─────────────┐
                │ DISCONNECTED │
                └──────┬───────┘
                       │
                  .connect()
                       │
                       ▼
                ┌─────────────┐
           ┌────│ CONNECTING  │◄────┐
           │    └──────┬───────┘     │
           │           │             │
    (error)│   (success)            │(reconnect)
           │           │             │
           │           ▼             │
           │    ┌─────────────┐     │
           └───►│  CONNECTED  │─────┘
                └─────────────┘
                       │
                  .disconnect()
                       │
                       ▼
                ┌─────────────┐
                │ DISCONNECTED │
                └─────────────┘
```

**Why state tracking matters:**

1. **UI indicators:** Show "Connecting...", "Connected", "Offline" badges
2. **Message handling:** Queue messages during CONNECTING, send during CONNECTED
3. **Subscription sync:** Re-subscribe to channels on reconnect
4. **Error recovery:** Different logic for transient vs. permanent errors

---

## 4. KEY DESIGN CONSIDERATIONS

### 4.1 Message Buffering

**Problem:** User sends message while disconnected. What happens?

**Options:**

| Approach | Pros | Cons | Use When |
|----------|------|------|----------|
| **Drop message** | Simple | Lost data, bad UX | Never |
| **Show error** | User aware | Requires retry UI | Low-value messages |
| **Queue & retry** | No data loss, good UX | Memory usage, complexity | Chat, notifications |
| **Local storage** | Survives page reload | Stale on reconnect | Offline-first apps |

**Recommendation for lobs-server:** **Queue & retry** (limited buffer)

```javascript
class WebSocketManager {
    constructor() {
        this.messageQueue = [];
        this.maxQueueSize = 100;  // Prevent memory leak
        this.state = 'disconnected';
    }
    
    send(message) {
        if (this.state === 'connected') {
            this.ws.send(JSON.stringify(message));
        } else {
            // Queue for later delivery
            if (this.messageQueue.length < this.maxQueueSize) {
                this.messageQueue.push(message);
            } else {
                console.warn('Message queue full, dropping message');
            }
        }
    }
    
    onReconnect() {
        // Flush queued messages
        while (this.messageQueue.length > 0) {
            const msg = this.messageQueue.shift();
            this.ws.send(JSON.stringify(msg));
        }
    }
}
```

### 4.2 Heartbeat / Ping-Pong

**Problem:** How to detect connection is dead without waiting for send() to fail?

**Solution:** Server sends periodic ping, client responds with pong.

**Implementation (server-side, FastAPI):**

```python
# In chat manager
async def heartbeat_loop(self, websocket: WebSocket, session_key: str):
    """Send ping every 30s to detect dead connections."""
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
            # If this fails, connection is dead
    except:
        self.disconnect(websocket, session_key)

# In websocket_endpoint
asyncio.create_task(heartbeat_loop(websocket, session_key))
```

**Client response:**

```javascript
ws.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'ping') {
        ws.send(JSON.stringify({type: 'pong'}));
    }
});
```

**Alternative:** Client detects missing pings

```javascript
let lastPingTime = Date.now();

ws.addEventListener('message', (event) => {
    if (data.type === 'ping') {
        lastPingTime = Date.now();
    }
});

setInterval(() => {
    if (Date.now() - lastPingTime > 60000) {
        console.warn('No ping in 60s, assuming connection dead');
        ws.close();  // Triggers reconnect
    }
}, 10000);
```

**Centrifuge-js approach:**

```javascript
const centrifuge = new Centrifuge('ws://...', {
    maxServerPingDelay: 10000  // Reconnect if no ping for 10s
});
```

### 4.3 Connection Quality Indicators

**Best practice:** Show connection state in UI

**Examples:**

**Subtle indicator (recommended):**
```html
<!-- Top-right corner badge -->
<div class="connection-badge" :class="connectionState">
    {{ connectionState === 'connected' ? '●' : connectionState }}
</div>

<style>
.connection-badge.connected { color: green; }
.connection-badge.connecting { color: orange; animation: pulse 1s infinite; }
.connection-badge.disconnected { color: red; }
</style>
```

**Prominent banner (for critical apps):**
```html
<div v-if="connectionState !== 'connected'" class="alert">
    {{ connectionState === 'connecting' ? 'Reconnecting...' : 'Connection lost. Retrying...' }}
</div>
```

**Message send feedback:**
```javascript
async function sendMessage(content) {
    if (wsManager.state !== 'connected') {
        // Show warning
        showNotification('Message will be sent when connection restored', 'warning');
    }
    
    await wsManager.send({type: 'send_message', content});
}
```

### 4.4 Authentication Token Refresh

**Challenge:** WebSocket connections are long-lived, but JWT tokens expire.

**Pattern 1: Server sends token expiry warning**

```python
# Server sends before token expires
await websocket.send_json({
    "type": "token_expiring",
    "expires_in": 300  # 5 minutes
})
```

```javascript
// Client refreshes token
ws.addEventListener('message', async (event) => {
    if (data.type === 'token_expiring') {
        const newToken = await fetch('/api/refresh-token').then(r => r.json());
        ws.send(JSON.stringify({type: 'update_token', token: newToken.token}));
    }
});
```

**Pattern 2: Client proactively refreshes**

```javascript
class WebSocketManager {
    constructor(url, tokenProvider) {
        this.tokenProvider = tokenProvider;  // async function
        this.tokenRefreshInterval = 15 * 60 * 1000; // 15 min
        
        setInterval(async () => {
            if (this.state === 'connected') {
                const newToken = await this.tokenProvider();
                this.send({type: 'update_token', token: newToken});
            }
        }, this.tokenRefreshInterval);
    }
}
```

**Pattern 3: Reconnect with fresh token (simplest)**

```javascript
// On reconnect, always fetch fresh token
async function connect() {
    const token = await fetch('/api/token').then(r => r.json());
    const ws = new WebSocket(`ws://localhost:8000/ws?token=${token.token}`);
    // ...
}
```

**Recommendation for lobs-server:** **Pattern 3** (reconnect with fresh token)
- Simplest implementation
- Token refresh is implicit in reconnection
- Current `/ws` endpoint already validates token on connect

### 4.5 Subscription State Management

**Challenge:** Client subscribed to channels. On reconnect, must re-establish subscriptions.

**Bad pattern:**
```javascript
// ❌ User must manually re-subscribe after reconnect
ws.addEventListener('open', () => {
    ws.send(JSON.stringify({type: 'subscribe', channel: 'news'}));
});
```

**Good pattern:**
```javascript
class SubscriptionManager {
    constructor(ws) {
        this.subscriptions = new Set();
        
        ws.addEventListener('open', () => {
            // Auto-resubscribe to all channels
            for (const channel of this.subscriptions) {
                ws.send(JSON.stringify({type: 'subscribe', channel}));
            }
        });
    }
    
    subscribe(channel) {
        this.subscriptions.add(channel);
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({type: 'subscribe', channel}));
        }
        // If not open, will be sent on reconnect
    }
    
    unsubscribe(channel) {
        this.subscriptions.delete(channel);
        ws.send(JSON.stringify({type: 'unsubscribe', channel}));
    }
}
```

**Current lobs-server pattern:**

From `chat.py`:
```python
elif event_type == "switch_session":
    new_session_key = data.get("session_key", "main")
    manager.disconnect(websocket, session_key)
    session_key = new_session_key
    await manager.connect(websocket, session_key)
```

**Gap:** Client doesn't track which session it's on. On reconnect, defaults to `?session_key=main`.

**Fix:** Client should remember current session and pass it on reconnect:

```javascript
let currentSessionKey = localStorage.getItem('lastSessionKey') || 'main';

function connect() {
    const ws = new WebSocket(`ws://localhost:8000/chat/ws?session_key=${currentSessionKey}&token=${token}`);
    // ...
}
```

---

## 5. COMPARISON: RECONNECTION STRATEGIES

### 5.1 Fixed Interval (Naive)

```javascript
function connect() {
    const ws = new WebSocket('ws://localhost:8000/ws');
    
    ws.onclose = () => {
        setTimeout(connect, 5000);  // Always retry after 5s
    };
}
```

**Pros:**
- ✅ Simple (4 lines of code)

**Cons:**
- ❌ Thundering herd (all clients retry simultaneously)
- ❌ No backoff (hammers server after crash)
- ❌ No max delay (wastes resources if server down for hours)

**Verdict:** ❌ Never use in production

### 5.2 Exponential Backoff (No Jitter)

```javascript
let retryCount = 0;

function connect() {
    const ws = new WebSocket('ws://localhost:8000/ws');
    
    ws.onopen = () => { retryCount = 0; };  // Reset on success
    
    ws.onclose = () => {
        const delay = Math.min(10000, 1000 * Math.pow(1.3, retryCount));
        retryCount++;
        setTimeout(connect, delay);
    };
}
```

**Pros:**
- ✅ Backs off under load
- ✅ Caps max delay

**Cons:**
- ❌ Still synchronized (all clients use same timing)
- ❌ No jitter

**Verdict:** ⚠️ Better than fixed, but not production-ready

### 5.3 Exponential Backoff + Jitter (Recommended)

```javascript
let retryCount = 0;

function connect() {
    const ws = new WebSocket('ws://localhost:8000/ws');
    
    ws.onopen = () => { retryCount = 0; };
    
    ws.onclose = () => {
        const baseDelay = Math.min(10000, 1000 * Math.pow(1.3, retryCount));
        const jitter = baseDelay * 0.25 * Math.random();  // ±25% jitter
        const delay = baseDelay + jitter;
        
        retryCount++;
        setTimeout(connect, delay);
    };
}
```

**Pros:**
- ✅ Backs off exponentially
- ✅ Jitter prevents thundering herd
- ✅ Production-ready

**Cons:**
- ❌ More complex (but still ~10 lines)

**Verdict:** ✅ **Recommended for manual implementation**

### 5.4 Library-based (reconnecting-websocket)

```javascript
import ReconnectingWebSocket from 'reconnecting-websocket';

const rws = new ReconnectingWebSocket('ws://localhost:8000/ws', [], {
    minReconnectionDelay: 1000,
    maxReconnectionDelay: 10000,
    reconnectionDelayGrowFactor: 1.3,
    maxRetries: Infinity,
});

// Use like normal WebSocket
rws.addEventListener('message', handleMessage);
```

**Pros:**
- ✅ Battle-tested (1.5M downloads/week)
- ✅ Zero implementation complexity
- ✅ Message buffering included
- ✅ Works everywhere (browser, React Native, Node.js)

**Cons:**
- ❌ External dependency (but small: 5KB gzipped)

**Verdict:** ✅ **Recommended for most projects**

---

## 6. RECOMMENDATIONS FOR LOBS-SERVER

### 6.1 Short-term (Quick Win)

**Use `reconnecting-websocket` library on client side.**

**Implementation:**

1. Install library:
```bash
npm install reconnecting-websocket
```

2. Wrap existing WebSocket code:

```javascript
// Before (in lobs-mission-control frontend)
const ws = new WebSocket(`ws://localhost:8000/chat/ws?session_key=${sessionKey}&token=${token}`);

// After
import ReconnectingWebSocket from 'reconnecting-websocket';

const options = {
    minReconnectionDelay: 1000,
    maxReconnectionDelay: 20000,
    reconnectionDelayGrowFactor: 1.3,
    connectionTimeout: 4000,
    maxRetries: Infinity,
    debug: import.meta.env.DEV  // Debug in dev mode
};

const rws = new ReconnectingWebSocket(
    `ws://localhost:8000/chat/ws?session_key=${sessionKey}&token=${token}`,
    [],
    options
);

// Rest of code unchanged (API compatible)
rws.addEventListener('open', () => { /* ... */ });
rws.addEventListener('message', handleMessage);
```

3. Add connection state indicator:

```vue
<!-- In ChatView.vue -->
<template>
    <div class="connection-status" :class="connectionState">
        <span v-if="connectionState === 'connecting'">Connecting...</span>
        <span v-if="connectionState === 'connected'">●</span>
        <span v-if="connectionState === 'closed'">Offline</span>
    </div>
</template>

<script>
export default {
    data() {
        return {
            connectionState: 'connecting'  // 'connecting' | 'connected' | 'closed'
        };
    },
    
    mounted() {
        this.rws.addEventListener('open', () => {
            this.connectionState = 'connected';
        });
        
        this.rws.addEventListener('close', () => {
            this.connectionState = 'closed';
        });
        
        this.rws.addEventListener('connecting', () => {
            this.connectionState = 'connecting';
        });
    }
}
</script>
```

**Effort:** 1-2 hours  
**Impact:** Massive UX improvement, production-ready reconnection

### 6.2 Medium-term (Better Token Handling)

**Problem:** Token in query string is passed on every reconnect. If token expired, reconnect fails.

**Solution:** Refresh token before reconnecting.

```javascript
import ReconnectingWebSocket from 'reconnecting-websocket';

class WebSocketManager {
    constructor(sessionKey, getTokenFn) {
        this.sessionKey = sessionKey;
        this.getTokenFn = getTokenFn;  // async () => string
        this.connect();
    }
    
    async connect() {
        // Get fresh token before each connection attempt
        const token = await this.getTokenFn();
        
        const url = `ws://localhost:8000/chat/ws?session_key=${this.sessionKey}&token=${token}`;
        
        this.rws = new ReconnectingWebSocket(url, [], {
            minReconnectionDelay: 1000,
            maxReconnectionDelay: 20000,
            reconnectionDelayGrowFactor: 1.3,
        });
        
        return this.rws;
    }
}

// Usage
const wsManager = new WebSocketManager('main', async () => {
    const response = await fetch('/api/token');
    const data = await response.json();
    return data.token;
});
```

**Effort:** 2-3 hours  
**Impact:** Never fails reconnect due to expired token

### 6.3 Long-term (Advanced Features)

**Heartbeat mechanism:**

Add server-side ping every 30s to detect dead connections faster.

```python
# In chat manager
async def start_heartbeat(self, websocket: WebSocket, session_key: str):
    try:
        while True:
            await asyncio.sleep(30)
            await manager.send_to_connection(websocket, {"type": "ping"})
    except:
        manager.disconnect(websocket, session_key)
```

**Message delivery guarantees:**

Track message IDs and confirm delivery.

```javascript
class ReliableWebSocket {
    constructor(url) {
        this.pendingMessages = new Map();  // id -> {message, timestamp}
        this.messageId = 0;
        
        this.rws = new ReconnectingWebSocket(url);
        
        this.rws.addEventListener('message', (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'ack') {
                this.pendingMessages.delete(data.messageId);
            }
        });
        
        this.rws.addEventListener('open', () => {
            // Resend pending messages
            for (const [id, {message}] of this.pendingMessages) {
                this.rws.send(JSON.stringify(message));
            }
        });
    }
    
    send(message) {
        const id = this.messageId++;
        message.id = id;
        
        this.pendingMessages.set(id, {message, timestamp: Date.now()});
        this.rws.send(JSON.stringify(message));
        
        // Clean up old pending messages (1 minute timeout)
        setTimeout(() => {
            if (this.pendingMessages.has(id)) {
                console.warn('Message', id, 'never acknowledged');
                this.pendingMessages.delete(id);
            }
        }, 60000);
    }
}
```

**Effort:** 1-2 days  
**Impact:** Enterprise-grade reliability

---

## 7. COMPARISON TABLE: RECONNECTION LIBRARIES

| Library | Stars | Downloads/week | Size | Key Features |
|---------|-------|----------------|------|--------------|
| **reconnecting-websocket** | 4.2K | 1.5M | 5KB | Simple, WebSocket-compatible API |
| **centrifuge-js** | 600 | 50K | 40KB | Full real-time framework, token refresh |
| **Socket.io-client** | 10K | 10M | 50KB | Full framework, fallbacks (long-polling) |
| **ws** (Node.js only) | 21K | 100M | N/A | Native WebSocket, no reconnect built-in |

**Recommendation:** `reconnecting-websocket` for lobs-server
- ✅ Lightweight (5KB vs 40-50KB for alternatives)
- ✅ Drop-in replacement (minimal code changes)
- ✅ Battle-tested (1.5M downloads/week)
- ✅ No framework lock-in

---

## 8. GOTCHAS & ANTI-PATTERNS

### 8.1 ❌ Don't: Infinite Retries Without User Control

```javascript
// Bad: User stuck in infinite loop
ws.onclose = () => {
    setTimeout(connect, 5000);  // Forever, no escape
};
```

**Better:**
```javascript
let manuallyDisconnected = false;

function disconnect() {
    manuallyDisconnected = true;
    ws.close();
}

ws.onclose = () => {
    if (!manuallyDisconnected) {
        setTimeout(connect, 5000);
    }
};
```

### 8.2 ❌ Don't: Reconnect on Every Close Code

```javascript
ws.onclose = (event) => {
    // Bad: Some close codes are permanent
    setTimeout(connect, 1000);
};
```

**Better:**
```javascript
ws.onclose = (event) => {
    // 1000 = normal close
    // 1001 = going away
    // 1006 = abnormal close (network issue)
    // 1008 = policy violation (bad auth)
    
    if (event.code === 1008) {
        console.error('Authentication failed, not reconnecting');
        return;  // Don't retry on auth failure
    }
    
    setTimeout(connect, 1000);
};
```

**Close codes reference (RFC 6455):**
- 1000: Normal closure
- 1001: Going away (page navigation)
- 1006: Abnormal (network failure) - **should reconnect**
- 1008: Policy violation (bad token) - **should NOT reconnect**
- 1011: Server error - **should reconnect**

### 8.3 ❌ Don't: Forget to Clean Up Timers

```javascript
let reconnectTimer;

function connect() {
    const ws = new WebSocket('ws://...');
    
    ws.onclose = () => {
        reconnectTimer = setTimeout(connect, 1000);  // Memory leak!
    };
}

// If user manually disconnects:
function disconnect() {
    ws.close();
    clearTimeout(reconnectTimer);  // ✅ Clean up!
}
```

### 8.4 ❌ Don't: Block Main Thread During Reconnect

```javascript
// Bad: Blocks UI
while (ws.readyState !== WebSocket.OPEN) {
    connect();
    sleep(1000);  // NEVER DO THIS
}
```

**Better:**
```javascript
// Good: Async reconnection
async function connectWithRetry() {
    while (true) {
        try {
            await connect();
            break;  // Success!
        } catch (err) {
            await sleep(1000);  // Non-blocking
        }
    }
}
```

---

## 9. TESTING RECOMMENDATIONS

### 9.1 How to Test Reconnection Logic

**Manual testing:**

1. **Server restart:**
   ```bash
   # Terminal 1: Run server
   uvicorn app.main:app
   
   # Terminal 2: Tail logs
   # Open app in browser, send message
   # Ctrl+C server → observe reconnect
   # Restart server → message should be delivered
   ```

2. **Network disconnect:**
   - Chrome DevTools → Network tab → Offline checkbox
   - Should see "Connecting..." indicator
   - Uncheck Offline → should reconnect

3. **Token expiration:**
   - Generate token with 30s expiry
   - Wait 31s
   - Send message → should fail if no token refresh

**Automated testing:**

```javascript
describe('WebSocket Reconnection', () => {
    it('reconnects after server restart', async () => {
        const ws = new ReconnectingWebSocket('ws://localhost:8000/ws');
        
        await waitForEvent(ws, 'open');
        expect(ws.readyState).toBe(WebSocket.OPEN);
        
        // Simulate server crash
        ws.close();
        
        // Should reconnect
        await waitForEvent(ws, 'open');
        expect(ws.readyState).toBe(WebSocket.OPEN);
    });
    
    it('uses exponential backoff', async () => {
        const ws = new ReconnectingWebSocket('ws://localhost:9999', [], {
            minReconnectionDelay: 100,
            maxReconnectionDelay: 1000,
        });
        
        const delays = [];
        ws.addEventListener('connecting', () => {
            delays.push(Date.now());
        });
        
        await sleep(5000);
        
        // Check delays are increasing
        for (let i = 1; i < delays.length; i++) {
            const delay = delays[i] - delays[i-1];
            expect(delay).toBeGreaterThan(100);  // Min delay
        }
    });
});
```

---

## 10. SUMMARY & ACTION ITEMS

### What We Learned

1. **WebSocket reconnection is not automatic** - must be implemented
2. **Exponential backoff + jitter** is the industry standard
3. **Production libraries exist** and are battle-tested
4. **Connection state tracking** is essential for good UX
5. **Message buffering** prevents data loss during disconnects

### Recommended Implementation Path

**Phase 1: Quick Win (1-2 hours)**
- ✅ Install `reconnecting-websocket` npm package
- ✅ Wrap existing WebSocket initialization
- ✅ Add connection state indicator to UI

**Phase 2: Token Refresh (2-3 hours)**
- ✅ Implement `getToken` callback for fresh tokens on reconnect
- ✅ Handle token expiry gracefully

**Phase 3: Polish (4-6 hours)**
- ✅ Add server-side heartbeat (ping/pong)
- ✅ Implement message delivery confirmation (optional)
- ✅ Add automated reconnection tests

### Configuration Recommendations

```javascript
const reconnectOptions = {
    // Fast initial retry
    minReconnectionDelay: 1000,  // 1s
    
    // Cap exponential growth
    maxReconnectionDelay: 20000,  // 20s
    
    // Gentle backoff
    reconnectionDelayGrowFactor: 1.3,
    
    // Detect connection timeout
    connectionTimeout: 4000,  // 4s
    
    // Never give up (network apps)
    maxRetries: Infinity,
    
    // Buffer up to 100 messages
    maxEnqueuedMessages: 100,
    
    // Debug in development
    debug: process.env.NODE_ENV === 'development'
};
```

### What NOT to Do

- ❌ Fixed retry intervals (thundering herd)
- ❌ No jitter (synchronized reconnects)
- ❌ No max delay (resource waste)
- ❌ Reconnect on auth failure (infinite loop)
- ❌ Block main thread (bad UX)

---

## References

1. **reconnecting-websocket library**  
   https://github.com/pladaria/reconnecting-websocket  
   1.5M downloads/week, 4.2K stars

2. **Centrifuge-js library**  
   https://github.com/centrifugal/centrifuge-js  
   Production real-time framework with advanced reconnection

3. **MDN WebSocket API**  
   https://developer.mozilla.org/en-US/docs/Web/API/WebSocket  
   Official WebSocket API documentation

4. **JavaScript.info WebSocket Tutorial**  
   https://javascript.info/websocket  
   Comprehensive WebSocket guide with examples

5. **RFC 6455 - WebSocket Protocol**  
   https://datatracker.ietf.org/doc/html/rfc6455  
   Official WebSocket specification (close codes, etc.)

6. **Current lobs-server implementation**  
   `/Users/lobs/lobs-server/app/routers/chat.py`  
   Existing WebSocket endpoint (FastAPI)

---

**Last updated:** February 14, 2026  
**Next steps:** Implement Phase 1 (reconnecting-websocket wrapper)
