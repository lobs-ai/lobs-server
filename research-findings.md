# WebSocket Reconnection Handling: Best Practices & Implementation Strategies

**Research Date:** 2026-02-14  
**Project:** lobs-server  
**Context:** Real-time WebSocket messaging with OpenClaw agent bridge

---

## Executive Summary

WebSocket connections are inherently fragile and will disconnect due to network issues, server restarts, client sleep/wake cycles, and other transient failures. Production-grade WebSocket implementations require robust auto-reconnection with exponential backoff and jitter to handle these scenarios gracefully.

**Key Findings:**
- **Exponential backoff with jitter** is the industry standard for reconnection
- **Full jitter** provides best balance of reduced server load and reasonable reconnection time
- **Maximum retry limits** prevent infinite reconnection loops
- **Connection state recovery** is critical for seamless user experience
- **Heartbeat/ping mechanisms** detect stale connections proactively

---

## 1. Core Concepts

### 1.1 Why WebSockets Disconnect

WebSocket connections can fail for numerous reasons ([javascript.info](https://javascript.info/websocket)):

- **Network failures**: WiFi/cellular handoff, packet loss, router issues
- **Server-side issues**: Deployments, restarts, scaling events, load balancer timeouts
- **Client-side issues**: Browser backgrounding, device sleep, mobile network changes
- **Proxy/intermediary problems**: Corporate firewalls, NAT timeouts, CDN issues
- **Ping timeout**: Server didn't send PING within `pingInterval + pingTimeout` ([Socket.io docs](https://socket.io/docs/v4/client-api/))

### 1.2 WebSocket Close Codes

Understanding close codes helps determine reconnection strategy ([RFC 6455](https://www.rfc-editor.org/rfc/rfc6455), [javascript.info](https://javascript.info/websocket)):

| Code | Meaning | Auto-Reconnect? |
|------|---------|----------------|
| 1000 | Normal closure | ❌ No |
| 1001 | Going away (server shutdown, page navigation) | ✅ Yes |
| 1006 | Abnormal closure (no close frame) | ✅ Yes |
| 1009 | Message too big | ❌ No |
| 1011 | Unexpected server error | ✅ Yes |

**Rule of thumb**: Reconnect on network/transport errors (1001, 1006, 1011+), but NOT on intentional closes (1000) or protocol violations (1002-1009).

---

## 2. Exponential Backoff Strategies

### 2.1 Why Exponential Backoff?

When multiple clients disconnect simultaneously (e.g., during a server deployment), naive immediate reconnection causes a **thundering herd problem**: all clients hammer the recovering server at once, potentially causing cascading failures.

Exponential backoff solves this by:
- **Spreading load** over time
- **Reducing contention** for server resources
- **Allowing transient issues to resolve** before retry

### 2.2 Backoff Algorithm Variants

Based on [AWS Architecture Blog](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) and [Wikipedia: Exponential Backoff](https://en.wikipedia.org/wiki/Exponential_backoff):

#### **A. Simple Exponential Backoff (Not Recommended)**
```javascript
delay = min(maxDelay, baseDelay * (2 ** attemptNumber))
```
**Problem**: All clients retry at the same intervals, causing synchronized waves of requests.

#### **B. Full Jitter (Recommended)**
```javascript
delay = random(0, min(maxDelay, baseDelay * (2 ** attemptNumber)))
```
**Benefits**:
- **50% reduction** in total client work vs. no jitter (AWS study)
- Randomization prevents synchronized retries
- Best for high-contention scenarios

#### **C. Equal Jitter**
```javascript
temp = min(maxDelay, baseDelay * (2 ** attemptNumber))
delay = temp / 2 + random(0, temp / 2)
```
**Benefits**: Always maintains some minimum delay, preventing very short sleeps

#### **D. Decorrelated Jitter**
```javascript
delay = min(maxDelay, random(baseDelay, prevDelay * 3))
```
**Benefits**: Bases next delay on previous, creating more natural spread

### 2.3 Recommended Parameters

Based on [reconnecting-websocket](https://github.com/joewalnes/reconnecting-websocket) and [Socket.io](https://socket.io/docs/v4/client-api/):

| Parameter | Typical Value | Purpose |
|-----------|---------------|---------|
| `reconnectInterval` | 1000ms (1s) | Initial retry delay |
| `maxReconnectInterval` | 30000ms (30s) | Cap on retry delay |
| `reconnectDecay` | 1.5 | Exponential growth rate |
| `timeoutInterval` | 2000ms (2s) | Connection attempt timeout |
| `maxReconnectAttempts` | `null` (infinite) or `10-20` | Prevents infinite loops |

**Example progression with decay=1.5**:
1. 1s
2. 1.5s  
3. 2.25s
4. 3.375s
5. 5.06s
6. 7.59s
7. 11.39s
8. 17.09s
9. 25.63s
10. 30s (capped)

---

## 3. Production Implementation Patterns

### 3.1 Socket.io Approach

Socket.io ([docs](https://socket.io/docs/v4/client-api/)) implements sophisticated reconnection with:

**Auto-Reconnection Triggers** ([Event: 'disconnect'](https://socket.io/docs/v4/client-api/#event-disconnect)):
- ✅ `ping timeout` - Server didn't send PING
- ✅ `transport close` - Network disconnection
- ✅ `transport error` - Connection error
- ❌ `io server disconnect` - Server explicitly closed
- ❌ `io client disconnect` - Client called `disconnect()`

**Key Features**:
- **Connection state recovery** (`socket.recovered`) - missed events replayed on reconnect
- **Randomized delay** - `reconnectionDelay` with jitter
- **Exponential growth** - up to `reconnectionDelayMax`
- **Attempt counting** - fires `reconnect_attempt` event with count

**Configuration**:
```javascript
const socket = io({
  reconnection: true,
  reconnectionDelay: 1000,        // starts at 1s
  reconnectionDelayMax: 5000,     // caps at 5s  
  reconnectionAttempts: Infinity, // never give up
  randomizationFactor: 0.5        // jitter: ±50%
});
```

### 3.2 reconnecting-websocket Library

[reconnecting-websocket](https://github.com/joewalnes/reconnecting-websocket) provides a minimal decorator:

```javascript
const ws = new ReconnectingWebSocket('wss://example.com', null, {
  reconnectInterval: 1000,
  maxReconnectInterval: 30000,
  reconnectDecay: 1.5,
  timeoutInterval: 2000,
  maxReconnectAttempts: null,
  debug: false,
  automaticOpen: true
});

// API-compatible with native WebSocket
ws.addEventListener('open', () => console.log('Connected'));
ws.addEventListener('message', (event) => console.log(event.data));
```

**Key Characteristics**:
- Drop-in replacement for native WebSocket
- Exponential backoff with configurable decay
- Less than 600 bytes gzipped
- No external dependencies

### 3.3 Ably Real-time Client

[Ably SDK](https://sdk.ably.com/builds/ably/specification/main/features/) demonstrates enterprise-grade reconnection:

**Fallback Host Strategy**:
- Primary domain: `main.realtime.ably.net`
- Fallback domains: `main.{a,b,c,d,e}.fallback.ably-realtime.com`
- Random fallback selection on failure
- Per-fallback timeout tracking
- Automatic retry across datacenters

**Connection State Machine**:
- `CONNECTING` → `CONNECTED` → `DISCONNECTED` → `SUSPENDED` → `FAILED`
- Different reconnection logic per state
- Exponential backoff increases with each state transition

---

## 4. Heartbeat & Connection Health

### 4.1 Ping/Pong Mechanism

WebSocket protocol includes built-in heartbeat ([RFC 6455](https://www.rfc-editor.org/rfc/rfc6455), [javascript.info](https://javascript.info/websocket)):

**Server-side** (most common):
```python
# FastAPI/Starlette WebSocket
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Send ping every 30s
            await websocket.send_text('{"type": "ping"}')
            await asyncio.sleep(30)
            
            # Wait for pong with timeout
            try:
                msg = await asyncio.wait_for(
                    websocket.receive_text(), 
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                # No pong received, connection is stale
                await websocket.close(code=1001)
                break
    except WebSocketDisconnect:
        pass
```

**Client-side**:
```javascript
ws.addEventListener('message', (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === 'ping') {
    ws.send(JSON.stringify({type: 'pong'}));
  }
});
```

### 4.2 Detecting Stale Connections

Per [Martin Fowler: HeartBeat pattern](https://martinfowler.com/articles/patterns-of-distributed-systems/heartbeat.html):

**Problem**: Network partitions can leave both ends thinking the connection is alive when it's actually dead.

**Solution**: 
- Server sends heartbeat at regular interval `T`
- Client expects heartbeat within `T + timeout`
- If no heartbeat received, client initiates reconnection

**Recommended intervals**:
- `pingInterval`: 25-30 seconds (less than typical proxy timeouts)
- `pingTimeout`: 5-10 seconds (network RTT + processing)
- Total window: ~35 seconds before declaring connection dead

---

## 5. Connection State Recovery

### 5.1 The Problem

When WebSocket disconnects and reconnects, naive implementations lose:
- **In-flight messages** not yet sent
- **Pending acknowledgements** 
- **Events received during disconnect**
- **Subscription state** on channels

This creates poor UX: missing chat messages, duplicate notifications, inconsistent state.

### 5.2 Solutions

#### **A. Client-Side Message Queue**
```javascript
class ResilientWebSocket {
  constructor(url) {
    this.url = url;
    this.queue = [];
    this.connected = false;
    this.connect();
  }
  
  send(data) {
    if (this.connected) {
      this.ws.send(data);
    } else {
      this.queue.push(data);
    }
  }
  
  onOpen() {
    this.connected = true;
    // Flush queued messages
    while (this.queue.length > 0) {
      this.ws.send(this.queue.shift());
    }
  }
}
```

#### **B. Server-Side State Reconciliation**

Socket.io's connection recovery ([docs](https://socket.io/docs/v4/client-api/)):
```javascript
socket.on('connect', () => {
  if (socket.recovered) {
    // Connection was recovered, server replayed missed events
    console.log('State synchronized');
  } else {
    // New/unrecoverable session, must re-subscribe
    socket.emit('subscribe', channels);
  }
});
```

Server tracks:
- Last acknowledged message ID per client
- Message buffer (time-limited)
- On reconnect, replay messages since last ack

#### **C. Idempotency Tokens**

For critical operations (payments, mutations):
```javascript
const idempotencyKey = generateUUID();
ws.send(JSON.stringify({
  type: 'order',
  idempotencyKey,
  data: orderData
}));
```

Server deduplicates based on `idempotencyKey` within time window.

---

## 6. Implementation Example (Python/FastAPI)

### 6.1 Server-Side (FastAPI/Starlette)

```python
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from datetime import datetime
import random

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    # Heartbeat task
    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(25)  # Ping every 25s
                await websocket.send_json({
                    "type": "ping",
                    "timestamp": datetime.utcnow().isoformat()
                })
        except:
            pass
    
    heartbeat_task = asyncio.create_task(heartbeat())
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg.get('type') == 'pong':
                # Heartbeat acknowledged
                continue
            
            # Handle other messages
            await manager.broadcast(data)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        heartbeat_task.cancel()
```

### 6.2 Client-Side (JavaScript)

```javascript
class ResilientWebSocket extends EventTarget {
  constructor(url, options = {}) {
    super();
    this.url = url;
    this.options = {
      reconnectInterval: 1000,
      maxReconnectInterval: 30000,
      reconnectDecay: 1.5,
      timeoutInterval: 2000,
      maxReconnectAttempts: null,
      ...options
    };
    
    this.reconnectAttempts = 0;
    this.messageQueue = [];
    this.shouldReconnect = true;
    
    this.connect();
  }
  
  connect() {
    this.ws = new WebSocket(this.url);
    
    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.reconnectAttempts = 0;
      
      // Flush message queue
      while (this.messageQueue.length > 0) {
        this.ws.send(this.messageQueue.shift());
      }
      
      this.dispatchEvent(new Event('open'));
    };
    
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      
      // Handle heartbeat
      if (msg.type === 'ping') {
        this.ws.send(JSON.stringify({type: 'pong'}));
        return;
      }
      
      this.dispatchEvent(new MessageEvent('message', {data: event.data}));
    };
    
    this.ws.onclose = (event) => {
      console.log('WebSocket closed:', event.code, event.reason);
      
      if (this.shouldReconnect && this.shouldAttemptReconnect(event.code)) {
        this.scheduleReconnect();
      } else {
        this.dispatchEvent(new CloseEvent('close', {
          code: event.code,
          reason: event.reason
        }));
      }
    };
    
    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      this.dispatchEvent(new Event('error'));
    };
  }
  
  shouldAttemptReconnect(closeCode) {
    // Don't reconnect on normal closure or protocol errors
    if (closeCode === 1000) return false;
    if (closeCode >= 1002 && closeCode <= 1009) return false;
    
    // Check attempt limit
    const {maxReconnectAttempts} = this.options;
    if (maxReconnectAttempts !== null && 
        this.reconnectAttempts >= maxReconnectAttempts) {
      return false;
    }
    
    return true;
  }
  
  scheduleReconnect() {
    this.reconnectAttempts++;
    
    const {
      reconnectInterval,
      maxReconnectInterval,
      reconnectDecay
    } = this.options;
    
    // Exponential backoff with full jitter
    const maxDelay = Math.min(
      maxReconnectInterval,
      reconnectInterval * Math.pow(reconnectDecay, this.reconnectAttempts - 1)
    );
    const delay = Math.random() * maxDelay;
    
    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    
    setTimeout(() => this.connect(), delay);
  }
  
  send(data) {
    if (this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(data);
    } else {
      // Queue messages while disconnected
      this.messageQueue.push(data);
    }
  }
  
  close() {
    this.shouldReconnect = false;
    this.ws.close(1000);
  }
}

// Usage
const ws = new ResilientWebSocket('wss://example.com/ws');

ws.addEventListener('open', () => {
  console.log('Connected!');
});

ws.addEventListener('message', (event) => {
  console.log('Message:', event.data);
});

ws.send(JSON.stringify({type: 'chat', message: 'Hello'}));
```

---

## 7. Common Gotchas & Anti-Patterns

### ❌ **Anti-Pattern 1: Immediate Reconnect on Every Failure**
```javascript
ws.onclose = () => {
  new WebSocket(url); // Instant retry → thundering herd!
};
```
**Fix**: Always use exponential backoff.

### ❌ **Anti-Pattern 2: No Reconnection Limit**
```javascript
// Infinite loop if server permanently rejects connection
while (true) {
  try { connect(); } catch { sleep(1000); }
}
```
**Fix**: Cap attempts or detect auth failures.

### ❌ **Anti-Pattern 3: Ignoring Close Codes**
```javascript
ws.onclose = () => scheduleReconnect(); // Reconnects even on 1000 (normal)
```
**Fix**: Only reconnect on transient failures (see table in §1.2).

### ❌ **Anti-Pattern 4: No Heartbeat**
```javascript
// Connection looks alive but is actually severed by proxy
ws.readyState === WebSocket.OPEN // → true, but no data flows
```
**Fix**: Implement ping/pong with timeout (see §4.1).

### ❌ **Anti-Pattern 5: Losing Messages on Disconnect**
```javascript
ws.send(data); // Fails silently if connection just closed
```
**Fix**: Queue messages and flush on reconnect (see §5.2).

---

## 8. Recommendations for lobs-server

Based on the research findings and the project context (FastAPI + SQLite REST API with WebSocket chat):

### 8.1 Server Implementation

1. **Ping/Pong Heartbeat**
   - Send ping every 25 seconds
   - Close connection if no pong received within 5 seconds
   - Use JSON message format for cross-platform compatibility

2. **Connection State Tracking**
   - Store `client_id`, `last_seen`, `last_message_id`
   - Implement message buffer (last 100 messages or 5 minute window)
   - Enable connection recovery on reconnect

3. **Graceful Shutdown**
   - Send close code 1001 ("going away") during deployments
   - Allows clients to immediately reconnect to healthy instance

### 8.2 Client Implementation

1. **Exponential Backoff with Full Jitter**
   ```javascript
   reconnectInterval: 1000,      // 1s initial
   maxReconnectInterval: 30000,  // 30s cap
   reconnectDecay: 1.5,          // growth rate
   maxReconnectAttempts: 20      // prevent infinite loops
   ```

2. **Message Queuing**
   - Queue messages while `readyState !== OPEN`
   - Flush queue on `onopen` event
   - Add message IDs for deduplication

3. **Connection State UI**
   ```javascript
   // Show connection status to user
   ws.addEventListener('open', () => showStatus('Connected'));
   ws.addEventListener('close', () => showStatus('Reconnecting...'));
   ws.addEventListener('error', () => showStatus('Connection error'));
   ```

4. **Heartbeat Response**
   - Auto-respond to `{type: 'ping'}` with `{type: 'pong'}`
   - Don't surface pings to application layer

### 8.3 Testing Strategy

1. **Network Resilience Tests**
   - Simulate network disconnects (kill connection)
   - Test server restarts (graceful close)
   - Proxy timeout simulation (70+ second idle)

2. **Load Tests**
   - 100+ clients disconnecting simultaneously
   - Verify backoff prevents thundering herd
   - Monitor reconnection distribution

3. **Message Integrity Tests**
   - Send messages during disconnect
   - Verify queuing and replay
   - Test duplicate detection

---

## 9. Further Reading

### Primary Sources
- **RFC 6455 - WebSocket Protocol**: https://www.rfc-editor.org/rfc/rfc6455
- **MDN WebSocket API**: https://developer.mozilla.org/en-US/docs/Web/API/WebSocket
- **javascript.info WebSocket Tutorial**: https://javascript.info/websocket

### Exponential Backoff
- **Wikipedia: Exponential Backoff**: https://en.wikipedia.org/wiki/Exponential_backoff
- **AWS: Exponential Backoff and Jitter**: https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
- **Amazon Builders' Library - Timeouts, Retries, Backoff**: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/

### Production Implementations
- **Socket.io Client API**: https://socket.io/docs/v4/client-api/
- **reconnecting-websocket**: https://github.com/joewalnes/reconnecting-websocket
- **Ably SDK Specification**: https://sdk.ably.com/builds/ably/specification/main/features/

### Distributed Systems Patterns
- **Martin Fowler: HeartBeat**: https://martinfowler.com/articles/patterns-of-distributed-systems/heartbeat.html

---

## 10. Conclusion

WebSocket reconnection is a solved problem with well-established best practices:

✅ **Use exponential backoff with full jitter** (random delay from 0 to max)  
✅ **Implement ping/pong heartbeats** (25-30s interval)  
✅ **Respect close codes** (don't reconnect on normal closure)  
✅ **Queue messages during disconnect** (flush on reconnect)  
✅ **Limit reconnection attempts** (prevent infinite loops)  
✅ **Show connection state to users** (transparency builds trust)

The cost of not implementing proper reconnection is poor user experience: lost messages, confused users, and support tickets. The cost of implementing it correctly is minimal—existing libraries like `reconnecting-websocket` and Socket.io do the heavy lifting.

For **lobs-server**, I recommend:
1. Adopt the full jitter exponential backoff pattern
2. Implement server-side heartbeat (FastAPI WebSocket already supports this)
3. Add client-side message queuing
4. Track connection state for potential message replay
5. Test thoroughly with network simulation tools

**Next steps**: Review the code examples in §6 and adapt them to your FastAPI WebSocket implementation. Consider using Socket.io for automatic reconnection if building a browser-based client, or implement the `ResilientWebSocket` pattern for custom clients.

---

**Document Version**: 1.0  
**Last Updated**: 2026-02-14  
**Author**: Researcher Agent  
**Status**: Complete
