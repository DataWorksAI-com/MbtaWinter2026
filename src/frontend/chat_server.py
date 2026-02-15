# src/frontend/chat_server.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict
import httpx
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

app = FastAPI(title="MBTA Chat UI")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
EXCHANGE_AGENT_URL = "http://exchange:8100"

# Mount static files for images
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

class ConnectionManager:
    """Manages WebSocket connections"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New WebSocket connection. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")
    
    async def send_message(self, message: Dict, websocket: WebSocket):
        await websocket.send_json(message)

manager = ConnectionManager()

@app.get("/")
async def get_ui():
    """Serve the enhanced chat UI with Christmas theme"""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MBTA Agntcy</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background-image: url('/static/christmas-bg.jpg');
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
            background-repeat: no-repeat;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            overflow: hidden;
            position: relative;
        }
        
        /* Festive overlay with slight blur effect */
        body::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: radial-gradient(circle at top, rgba(255,255,255,0.08) 0%, rgba(102,126,234,0.15) 100%);
            backdrop-filter: blur(2px);
            pointer-events: none;
            z-index: 0;
        }
        
        .main-container {
            width: 95%;
            max-width: 1600px;
            height: 90vh;
            display: grid;
            grid-template-columns: 1fr 400px;
            gap: 20px;
            position: relative;
            z-index: 1;
        }
        
        .chat-container {
            background: rgba(255, 255, 255, 0.98);
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            backdrop-filter: blur(10px);
        }
        
        .system-panel {
            background: rgba(255, 255, 255, 0.97);
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
            padding: 20px;
            overflow-y: auto;
            backdrop-filter: blur(10px);
        }
        
        .chat-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            font-size: 24px;
            font-weight: bold;
            text-align: center;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .header-left {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .status-indicator {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #4ade80;
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 30px;
            background: rgba(249, 250, 251, 0.95);
        }
        
        .message {
            margin-bottom: 20px;
            display: flex;
            animation: slideIn 0.3s ease-out;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .message.user {
            justify-content: flex-end;
        }
        
        .message-content {
            max-width: 70%;
            padding: 15px 20px;
            border-radius: 18px;
            word-wrap: break-word;
            white-space: pre-wrap;
        }
        
        .message.user .message-content {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 4px;
        }
        
        .message.assistant .message-content {
            background: white;
            color: #1f2937;
            border: 1px solid #e5e7eb;
            border-bottom-left-radius: 4px;
        }
        
        .message-metadata {
            font-size: 11px;
            color: #9ca3af;
            margin-top: 5px;
        }
        
        .chat-input-container {
            padding: 20px;
            background: white;
            border-top: 1px solid #e5e7eb;
        }
        
        .chat-input-wrapper {
            display: flex;
            gap: 10px;
        }
        
        #messageInput {
            flex: 1;
            padding: 15px 20px;
            border: 2px solid #e5e7eb;
            border-radius: 25px;
            font-size: 15px;
            outline: none;
            transition: border-color 0.3s;
        }
        
        #messageInput:focus {
            border-color: #667eea;
        }
        
        #sendButton {
            padding: 15px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 25px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        #sendButton:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
        }
        
        #sendButton:active {
            transform: translateY(0);
        }
        
        #sendButton:disabled {
            background: #9ca3af;
            cursor: not-allowed;
            transform: none;
        }
        
        .typing-indicator {
            display: none;
            padding: 15px 20px;
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 18px;
            border-bottom-left-radius: 4px;
            width: fit-content;
        }
        
        .typing-indicator.active {
            display: block;
        }
        
        .typing-indicator span {
            height: 8px;
            width: 8px;
            background: #9ca3af;
            border-radius: 50%;
            display: inline-block;
            margin: 0 2px;
            animation: typing 1.4s infinite;
        }
        
        .typing-indicator span:nth-child(2) {
            animation-delay: 0.2s;
        }
        
        .typing-indicator span:nth-child(3) {
            animation-delay: 0.4s;
        }
        
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-10px); }
        }
        
        /* System Panel Styles */
        .system-header {
            font-size: 20px;
            font-weight: bold;
            color: #1f2937;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }
        
        .system-card {
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            animation: fadeIn 0.3s ease-out;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateX(20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        
        .system-card-header {
            font-weight: 600;
            color: #667eea;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }
        
        .badge.mcp {
            background: #dcfce7;
            color: #15803d;
        }
        
        .badge.a2a {
            background: #dbeafe;
            color: #1e40af;
        }
        
        .badge.success {
            background: #dcfce7;
            color: #15803d;
        }
        
        .badge.pending {
            background: #fef3c7;
            color: #92400e;
        }
        
        .system-detail {
            font-size: 13px;
            color: #6b7280;
            margin: 5px 0;
        }
        
        .system-detail strong {
            color: #1f2937;
        }
        
        .agent-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;
        }
        
        .agent-chip {
            background: #f3f4f6;
            color: #4b5563;
            padding: 6px 12px;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 500;
        }
        
        .metrics {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-top: 10px;
        }
        
        .metric {
            background: #f9fafb;
            padding: 10px;
            border-radius: 8px;
            text-align: center;
        }
        
        .metric-value {
            font-size: 20px;
            font-weight: bold;
            color: #667eea;
        }
        
        .metric-label {
            font-size: 11px;
            color: #6b7280;
            text-transform: uppercase;
        }
        
        @media (max-width: 1200px) {
            .main-container {
                grid-template-columns: 1fr;
                grid-template-rows: 1fr auto;
            }
            
            .system-panel {
                max-height: 300px;
            }
        }

        /* Little moving train at the bottom */
        .train-layer {
            position: fixed;
            bottom: 8px;
            left: 0;
            width: 100%;
            pointer-events: none;
            z-index: 0;
        }

        .train-track {
            position: relative;
            width: 100%;
            height: 40px;
        }

        .train-rail {
            position: absolute;
            bottom: 6px;
            left: 0;
            width: 100%;
            height: 4px;
            background: repeating-linear-gradient(
                to right,
                rgba(15, 23, 42, 0.7) 0,
                rgba(15, 23, 42, 0.7) 20px,
                transparent 20px,
                transparent 40px
            );
            opacity: 0.9;
        }

        .train {
            position: absolute;
            bottom: 14px;
            width: 80px;
            height: 26px;
            margin-left: -100px;
            background: #111827;
            border-radius: 8px;
            box-shadow: 0 4px 0 rgba(15, 23, 42, 0.8);
            animation: trainRide 15s linear infinite;
        }

        .train::before {
            content: "";
            position: absolute;
            left: 8px;
            top: 6px;
            width: 22px;
            height: 12px;
            background: #fde047;
            border-radius: 4px;
            box-shadow: 0 0 8px rgba(253, 224, 71, 0.6);
        }

        .train::after {
            content: "";
            position: absolute;
            bottom: -5px;
            left: 10px;
            width: 60px;
            height: 5px;
            background: repeating-linear-gradient(
                to right,
                #4b5563 0,
                #4b5563 6px,
                transparent 6px,
                transparent 12px
            );
        }

        @keyframes trainRide {
            0%   { transform: translateX(0); }
            100% { transform: translateX(110vw); }
        }

        /* Falling snow animation */
        .snowflake {
            position: fixed;
            top: -10px;
            z-index: 9999;
            color: white;
            font-size: 1em;
            animation: fall linear infinite;
            pointer-events: none;
        }

        @keyframes fall {
            to {
                transform: translateY(100vh);
            }
        }
    </style>
</head>
<body>
    <div class="main-container">
        <!-- Chat Container -->
        <div class="chat-container">
            <div class="chat-header">
                <div class="header-left">
                    <div class="status-indicator"></div>
                    MBTA Agntcy
                </div>
            </div>
            
            <div class="chat-messages" id="messages">
                <div style="text-align: center; color: #6b7280; padding: 40px;">
                    <h2 style="margin-bottom: 10px; color: #1f2937;">👋 Welcome to MBTA Agntcy!</h2>
                    <p>Ask me anything about Boston's transit system</p>
                    <p style="font-size: 13px; margin-top: 20px; opacity: 0.7;">
                        Watch the system panel on the right to see how your queries are processed! →
                    </p>
                </div>
                <div class="typing-indicator" id="typingIndicator">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
            
            <div class="chat-input-container">
                <div class="chat-input-wrapper">
                    <input 
                        type="text" 
                        id="messageInput" 
                        placeholder="Ask about routes, schedules, alerts..."
                        onkeypress="handleKeyPress(event)"
                    />
                    <button id="sendButton" onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>

        <!-- System Visibility Panel -->
        <div class="system-panel">
            <div class="system-header">⚙️ System Internals</div>
            <div id="systemLog"></div>
        </div>
    </div>

    <!-- Little moving train layer -->
    <div class="train-layer">
        <div class="train-track">
            <div class="train-rail"></div>
            <div class="train"></div>
        </div>
    </div>

    <script>
        let ws;
        let conversationId = null;
        
        // Create falling snow
        function createSnowflakes() {
            const snowflakeCount = 50;
            for (let i = 0; i < snowflakeCount; i++) {
                setTimeout(() => {
                    const snowflake = document.createElement('div');
                    snowflake.className = 'snowflake';
                    snowflake.textContent = '❄';
                    snowflake.style.left = Math.random() * 100 + '%';
                    snowflake.style.animationDuration = (Math.random() * 3 + 2) + 's';
                    snowflake.style.opacity = Math.random();
                    snowflake.style.fontSize = (Math.random() * 10 + 10) + 'px';
                    
                    document.body.appendChild(snowflake);
                    
                    // Remove after animation
                    setTimeout(() => {
                        snowflake.remove();
                    }, 5000);
                }, i * 100);
            }
            
            // Create new snowflakes every 5 seconds
            setInterval(() => {
                const snowflake = document.createElement('div');
                snowflake.className = 'snowflake';
                snowflake.textContent = '❄';
                snowflake.style.left = Math.random() * 100 + '%';
                snowflake.style.animationDuration = (Math.random() * 3 + 2) + 's';
                snowflake.style.opacity = Math.random();
                snowflake.style.fontSize = (Math.random() * 10 + 10) + 'px';
                
                document.body.appendChild(snowflake);
                
                setTimeout(() => {
                    snowflake.remove();
                }, 5000);
            }, 100);
        }
        
        function connect() {
            ws = new WebSocket(`ws://${window.location.host}/ws`);
            
            ws.onopen = () => {
                console.log('Connected to MBTA Agntcy');
                addSystemLog('system', 'WebSocket connected', {status: 'active'});
            };
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                handleMessage(data);
            };
            
            ws.onclose = () => {
                console.log('Disconnected. Reconnecting...');
                addSystemLog('system', 'Connection lost. Reconnecting...', {status: 'reconnecting'});
                setTimeout(connect, 3000);
            };
            
            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        }
        
        function handleMessage(data) {
            if (data.type === 'response') {
                hideTypingIndicator();
                addMessage('assistant', data.content, data.metadata);
                conversationId = data.conversation_id;
            } else if (data.type === 'system') {
                addSystemLog(data.category, data.message, data.details);
            } else if (data.type === 'error') {
                hideTypingIndicator();
                addMessage('assistant', '❌ ' + data.error, {error: true});
            }
        }
        
        function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            
            if (message && ws.readyState === WebSocket.OPEN) {
                addMessage('user', message);
                showTypingIndicator();
                
                // Add system log for user query
                addSystemLog('input', 'User query received', {
                    query: message,
                    length: message.length
                });
                
                ws.send(JSON.stringify({
                    message: message,
                    conversation_id: conversationId
                }));
                
                input.value = '';
            }
        }
        
        function addMessage(role, content, metadata = {}) {
            const messagesDiv = document.getElementById('messages');
            const welcomeMessage = messagesDiv.querySelector('div[style]');
            if (welcomeMessage) {
                welcomeMessage.remove();
            }
            
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}`;
            
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = content;
            
            if (metadata.agents_called && metadata.agents_called.length > 0) {
                const metadataDiv = document.createElement('div');
                metadataDiv.className = 'message-metadata';
                metadataDiv.textContent = `🤖 Agents: ${metadata.agents_called.join(', ')}`;
                contentDiv.appendChild(metadataDiv);
            }
            
            messageDiv.appendChild(contentDiv);
            messagesDiv.insertBefore(messageDiv, document.getElementById('typingIndicator'));
            
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        function addSystemLog(category, message, details = {}) {
            const systemLog = document.getElementById('systemLog');
            const card = document.createElement('div');
            card.className = 'system-card';
            
            let badgeClass = 'badge';
            let icon = '📊';
            
            if (category === 'routing') {
                badgeClass += details.path === 'mcp' ? ' mcp' : ' a2a';
                icon = details.path === 'mcp' ? '⚡' : '🔄';
            } else if (category === 'agents') {
                badgeClass += ' success';
                icon = '🤖';
            } else if (category === 'input') {
                badgeClass += ' pending';
                icon = '📝';
            }
            
            let html = `
                <div class="system-card-header">
                    <span>${icon}</span>
                    <span>${message}</span>
                    <span class="${badgeClass}">${details.path || category}</span>
                </div>
            `;
            
            if (category === 'routing') {
                html += `
                    <div class="system-detail">
                        <strong>Intent:</strong> ${details.intent || 'unknown'}
                    </div>
                    <div class="system-detail">
                        <strong>Path:</strong> ${details.path === 'mcp' ? 'MCP Fast Path' : 'A2A Agents'}
                    </div>
                    ${details.confidence ? `<div class="system-detail"><strong>Confidence:</strong> ${(details.confidence * 100).toFixed(0)}%</div>` : ''}
                    ${details.latency ? `<div class="system-detail"><strong>Latency:</strong> ${details.latency}ms</div>` : ''}
                `;
            } else if (category === 'agents') {
                html += `
                    <div class="system-detail">
                        <strong>Agents Called:</strong>
                    </div>
                    <div class="agent-list">
                        ${details.agents.map(a => `<div class="agent-chip">${a}</div>`).join('')}
                    </div>
                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-value">${details.count || 0}</div>
                            <div class="metric-label">Agents</div>
                        </div>
                        <div class="metric">
                            <div class="metric-value">${details.duration || 0}ms</div>
                            <div class="metric-label">Duration</div>
                        </div>
                    </div>
                `;
            } else if (category === 'input') {
                html += `
                    <div class="system-detail">"${details.query}"</div>
                    <div class="system-detail"><strong>Length:</strong> ${details.length} characters</div>
                `;
            }
            
            card.innerHTML = html;
            systemLog.insertBefore(card, systemLog.firstChild);
            
            // Keep only last 10 items
            while (systemLog.children.length > 10) {
                systemLog.removeChild(systemLog.lastChild);
            }
        }
        
        function showTypingIndicator() {
            document.getElementById('typingIndicator').classList.add('active');
            const messagesDiv = document.getElementById('messages');
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        function hideTypingIndicator() {
            document.getElementById('typingIndicator').classList.remove('active');
        }
        
        function handleKeyPress(event) {
            if (event.key === 'Enter') {
                sendMessage();
            }
        }
        
        // Connect on load
        connect();
        
        // Start snow animation
        createSnowflakes();
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            message = data.get('message', '')
            conversation_id = data.get('conversation_id')
            
            # Call Exchange Agent
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        f"{EXCHANGE_AGENT_URL}/chat",
                        json={
                            'query': message,
                            'conversation_id': conversation_id
                        },
                        timeout=30.0
                    )
                    response.raise_for_status()
                    result = response.json()
                    
                    # Send routing info to system panel
                    await manager.send_message({
                        'type': 'system',
                        'category': 'routing',
                        'message': 'Routing Decision',
                        'details': {
                            'intent': result.get('intent', 'unknown'),
                            'confidence': result.get('confidence', 0.0),
                            'path': result.get('path', 'unknown'),
                            'latency': result.get('latency_ms', 0)
                        }
                    }, websocket)
                    
                    # If using A2A path, show agent info
                    if result.get('path') == 'a2a':
                        import asyncio
                        await asyncio.sleep(0.3)
                        
                        metadata = result.get('metadata', {})
                        agents_called = metadata.get('agents_called', [])
                        
                        if agents_called:
                            await manager.send_message({
                                'type': 'system',
                                'category': 'agents',
                                'message': 'Multi-Agent Execution',
                                'details': {
                                    'agents': agents_called,
                                    'count': len(agents_called),
                                    'duration': result.get('latency_ms', 0)
                                }
                            }, websocket)
                    
                    # Send response back to client
                    await manager.send_message({
                        'type': 'response',
                        'content': result['response'],
                        'conversation_id': conversation_id,
                        'metadata': result.get('metadata', {})
                    }, websocket)
                    
                except httpx.HTTPError as e:
                    logger.error(f"Error calling exchange agent at {EXCHANGE_AGENT_URL}: {e}")
                    await manager.send_message({
                        'type': 'error',
                        'error': 'Failed to process message. Please try again.'
                    }, websocket)
                    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "frontend"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)