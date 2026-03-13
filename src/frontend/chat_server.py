
# src/frontend/chat_server.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict
import os
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
# Use docker-compose service URL when provided; fallback supports local dev.
EXCHANGE_AGENT_URL = os.getenv("EXCHANGE_AGENT_URL", "http://localhost:8100")

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
    """Serve the enhanced chat UI with real-time weather effects and protocol override"""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MBTA Agentcy - Transit Intelligence</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
            position: relative;
            overflow: hidden;
        }

        /* Weather Canvas */
        #weatherCanvas {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 1;
        }

        .container {
            width: 100%;
            max-width: 1400px;
            height: 90vh;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            display: grid;
            grid-template-columns: 1fr 400px;
            overflow: hidden;
            position: relative;
            z-index: 2;
            min-height: 0;
        }

        /* Left Panel - Chat */
        .chat-panel {
            display: grid;
            grid-template-rows: auto minmax(0, 1fr) auto auto;
            border-right: 1px solid #e0e0e0;
            min-height: 0;
            overflow: hidden;
        }

        .chat-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px 30px;
            font-size: 24px;
            font-weight: bold;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 15px;
        }

        .weather-indicator {
            font-size: 28px;
            animation: pulse 2s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.1); }
        }

        .connection-status {
            font-size: 12px;
            padding: 4px 12px;
            border-radius: 12px;
            background: rgba(255,255,255,0.2);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #ff4444;
        }

        .status-dot.connected {
            background: #00ff88;
        }

        .messages-container {
            overflow-y: auto;
            padding: 20px 30px;
            background: #f8f9fa;
            min-height: 0;
            overscroll-behavior: contain;
        }

        .message {
            margin-bottom: 20px;
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
            text-align: right;
        }

        .message-content {
            display: inline-block;
            max-width: 70%;
            padding: 12px 18px;
            border-radius: 18px;
            word-wrap: break-word;
        }

        .message.user .message-content {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 4px;
        }

        .message.assistant .message-content {
            background: white;
            color: #333;
            border: 1px solid #e0e0e0;
            border-bottom-left-radius: 4px;
            text-align: left;
        }

        .message.system {
            text-align: center;
        }

        .message.system .message-content {
            background: #fff3cd;
            color: #856404;
            border: 1px solid #ffeaa7;
            font-size: 13px;
            padding: 8px 14px;
        }

        /* Protocol Override Controls */
        .protocol-controls {
            padding: 15px 30px;
            background: #f0f0f0;
            border-top: 1px solid #e0e0e0;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .protocol-label {
            font-size: 13px;
            font-weight: 600;
            color: #555;
        }

        .protocol-button {
            padding: 8px 16px;
            border: 2px solid #ddd;
            background: white;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .protocol-button:hover {
            background: #f8f9fa;
            border-color: #667eea;
        }

        .protocol-button.active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: #667eea;
        }

        .protocol-button.active:hover {
            background: linear-gradient(135deg, #5568d3 0%, #653a8b 100%);
        }

        .protocol-icon {
            font-size: 14px;
        }

        /* Input Area */
        .input-area {
            padding: 20px 30px;
            background: white;
            border-top: 1px solid #e0e0e0;
        }

        .input-container {
            display: flex;
            gap: 10px;
        }

        #messageInput {
            flex: 1;
            padding: 14px 18px;
            border: 2px solid #e0e0e0;
            border-radius: 25px;
            font-size: 15px;
            outline: none;
            transition: border-color 0.3s;
        }

        #messageInput:focus {
            border-color: #667eea;
        }

        #sendButton {
            padding: 14px 30px;
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
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }

        #sendButton:active {
            transform: translateY(0);
        }

        #sendButton:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }

        /* Right Panel - System Internals */
        .internals-panel {
            background: #1a1a2e;
            color: #eee;
            display: flex;
            flex-direction: column;
            min-height: 0;
            overflow: hidden;
        }

        .internals-header {
            padding: 20px;
            background: #16213e;
            border-bottom: 1px solid #2a2a4e;
        }

        .internals-title {
            font-size: 18px;
            font-weight: bold;
            color: #fff;
            margin-bottom: 8px;
        }

        .internals-subtitle {
            font-size: 12px;
            color: #888;
        }

        .weather-info {
            margin-top: 10px;
            padding: 8px 12px;
            background: rgba(78, 205, 196, 0.1);
            border-left: 3px solid #4ecdc4;
            border-radius: 4px;
            font-size: 12px;
        }

        .weather-info-title {
            font-weight: 600;
            color: #4ecdc4;
            margin-bottom: 4px;
        }

        .weather-info-detail {
            color: #aaa;
            font-size: 11px;
        }

        .internals-content {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            min-height: 0;
            overscroll-behavior: contain;
        }

        .info-block {
            background: #16213e;
            border: 1px solid #2a2a4e;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }

        .info-label {
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .info-value {
            font-size: 14px;
            color: #fff;
            font-weight: 500;
        }

        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            margin-right: 6px;
            margin-bottom: 6px;
        }

        .badge.mcp {
            background: #4ecdc4;
            color: #1a1a2e;
        }

        .badge.a2a {
            background: #ff6b6b;
            color: white;
        }

        .badge.shortcut {
            background: #95e1d3;
            color: #1a1a2e;
        }

        .badge.fallback {
            background: #ffa07a;
            color: #1a1a2e;
        }

        .badge.override {
            background: #ffd93d;
            color: #1a1a2e;
            animation: pulseBadge 1.5s infinite;
        }

        @keyframes pulseBadge {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }

        .latency-bar {
            height: 6px;
            background: #2a2a4e;
            border-radius: 3px;
            margin-top: 8px;
            overflow: hidden;
        }

        .latency-fill {
            height: 100%;
            background: linear-gradient(90deg, #4ecdc4, #667eea);
            border-radius: 3px;
            transition: width 0.5s ease;
        }

        .agent-list {
            list-style: none;
        }

        .agent-item {
            padding: 8px 0;
            border-bottom: 1px solid #2a2a4e;
            font-size: 13px;
        }

        .agent-item:last-child {
            border-bottom: none;
        }

        /* Scrollbar styling */
        ::-webkit-scrollbar {
            width: 8px;
        }

        ::-webkit-scrollbar-track {
            background: #f1f1f1;
        }

        ::-webkit-scrollbar-thumb {
            background: #667eea;
            border-radius: 4px;
        }

        .internals-panel ::-webkit-scrollbar-track {
            background: #1a1a2e;
        }

        .internals-panel ::-webkit-scrollbar-thumb {
            background: #4ecdc4;
        }
    </style>
</head>
<body>
    <canvas id="weatherCanvas"></canvas>

    <div class="container">
        <!-- Left Panel: Chat -->
        <div class="chat-panel">
            <div class="chat-header">
                <div class="header-left">
                    <span>🚇 MBTA Agentcy</span>
                    <span class="weather-indicator" id="weatherIcon">☁️</span>
                </div>
                <div class="connection-status">
                    <span class="status-dot" id="statusDot"></span>
                    <span id="statusText">Connecting...</span>
                </div>
            </div>

            <div class="messages-container" id="messagesContainer">
                <div class="message system">
                    <div class="message-content">
                        Welcome to MBTA Agentcy! Ask about transit alerts, routes, or stations.
                    </div>
                </div>
            </div>

            <div class="protocol-controls">
                <span class="protocol-label">Routing Mode:</span>
                <button class="protocol-button active" data-protocol="auto" onclick="selectProtocol('auto')">
                    <span class="protocol-icon">🤖</span>
                    <span>Auto</span>
                </button>
                <button class="protocol-button" data-protocol="mcp" onclick="selectProtocol('mcp')">
                    <span class="protocol-icon">⚡</span>
                    <span>MCP</span>
                </button>
                <button class="protocol-button" data-protocol="a2a" onclick="selectProtocol('a2a')">
                    <span class="protocol-icon">🔄</span>
                    <span>A2A</span>
                </button>
            </div>

            <div class="input-area">
                <div class="input-container">
                    <input 
                        type="text" 
                        id="messageInput" 
                        placeholder="Ask about MBTA alerts, routes, or stations..."
                        onkeypress="handleKeyPress(event)"
                    >
                    <button id="sendButton" onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>

        <!-- Right Panel: System Internals -->
        <div class="internals-panel">
            <div class="internals-header">
                <div class="internals-title">System Internals</div>
                <div class="internals-subtitle">Real-time routing & execution metrics</div>
                <div class="weather-info" id="weatherInfo">
                    <div class="weather-info-title">Loading weather...</div>
                    <div class="weather-info-detail">Fetching Boston conditions...</div>
                </div>
            </div>

            <div class="internals-content" id="internalsContent">
                <div class="info-block">
                    <div class="info-label">Waiting for query...</div>
                    <div class="info-value" style="color: #888;">Send a message to see routing details</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let ws = null;
        let currentProtocol = 'auto';
        let currentWeather = null;

        // ============================================================
        // WEATHER EFFECTS SYSTEM
        // ============================================================

        const canvas = document.getElementById('weatherCanvas');
        const ctx = canvas.getContext('2d');

        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;

        window.addEventListener('resize', () => {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        });

        let particles = [];

        class Particle {
            constructor(type) {
                this.type = type;
                this.x = Math.random() * canvas.width;
                this.y = Math.random() * canvas.height - canvas.height;
                this.reset();
            }

            reset() {
                if (this.type === 'snow') {
                    this.speed = Math.random() * 1 + 0.5;
                    this.radius = Math.random() * 3 + 1;
                    this.wind = Math.random() * 0.5 - 0.25;
                    this.opacity = Math.random() * 0.6 + 0.4;
                } else if (this.type === 'rain') {
                    this.speed = Math.random() * 5 + 10;
                    this.length = Math.random() * 20 + 10;
                    this.opacity = Math.random() * 0.4 + 0.3;
                    this.wind = Math.random() * 2 - 1;
                } else if (this.type === 'cloud') {
                    this.speed = Math.random() * 0.3 + 0.1;
                    this.radius = Math.random() * 30 + 20;
                    this.opacity = Math.random() * 0.3 + 0.2;
                    this.y = Math.random() * canvas.height * 0.3;
                }
            }

            update() {
                if (this.type === 'snow') {
                    this.y += this.speed;
                    this.x += this.wind;

                    if (this.y > canvas.height) {
                        this.y = -10;
                        this.x = Math.random() * canvas.width;
                    }
                } else if (this.type === 'rain') {
                    this.y += this.speed;
                    this.x += this.wind;

                    if (this.y > canvas.height) {
                        this.y = -this.length;
                        this.x = Math.random() * canvas.width;
                    }
                } else if (this.type === 'cloud') {
                    this.x += this.speed;

                    if (this.x > canvas.width + this.radius) {
                        this.x = -this.radius;
                    }
                }
            }

            draw() {
                ctx.save();
                ctx.globalAlpha = this.opacity;

                if (this.type === 'snow') {
                    ctx.fillStyle = 'white';
                    ctx.beginPath();
                    ctx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
                    ctx.fill();
                } else if (this.type === 'rain') {
                    ctx.strokeStyle = '#a0c4ff';
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(this.x, this.y);
                    ctx.lineTo(this.x + this.wind * 2, this.y + this.length);
                    ctx.stroke();
                } else if (this.type === 'cloud') {
                    ctx.fillStyle = 'white';
                    ctx.beginPath();
                    ctx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
                    ctx.arc(this.x + this.radius * 0.5, this.y - this.radius * 0.3, this.radius * 0.7, 0, Math.PI * 2);
                    ctx.arc(this.x + this.radius, this.y, this.radius * 0.8, 0, Math.PI * 2);
                    ctx.fill();
                }

                ctx.restore();
            }
        }

        function setWeatherEffect(weatherCondition) {
            particles = [];
            
            const weatherMap = {
                'Clear': 'clear',
                'Clouds': 'cloudy',
                'Rain': 'rain',
                'Drizzle': 'rain',
                'Thunderstorm': 'rain',
                'Snow': 'snow',
                'Mist': 'cloudy',
                'Fog': 'cloudy',
                'Haze': 'cloudy'
            };

            const effect = weatherMap[weatherCondition] || 'clear';

            if (effect === 'snow') {
                for (let i = 0; i < 150; i++) {
                    particles.push(new Particle('snow'));
                }
            } else if (effect === 'rain') {
                for (let i = 0; i < 200; i++) {
                    particles.push(new Particle('rain'));
                }
            } else if (effect === 'cloudy') {
                for (let i = 0; i < 5; i++) {
                    particles.push(new Particle('cloud'));
                }
            }
        }

        function animateWeather() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            particles.forEach(particle => {
                particle.update();
                particle.draw();
            });

            requestAnimationFrame(animateWeather);
        }

        animateWeather();

        // ============================================================
        // FETCH REAL WEATHER
        // ============================================================

        async function fetchWeather() {
            try {
                const response = await fetch(`https://wttr.in/Boston?format=j1`);
                const data = await response.json();
                
                const current = data.current_condition[0];
                const weatherDesc = current.weatherDesc[0].value;
                const temp = current.temp_F;
                const feelsLike = current.FeelsLikeF;
                
                let condition = 'Clear';
                if (weatherDesc.toLowerCase().includes('snow')) {
                    condition = 'Snow';
                } else if (weatherDesc.toLowerCase().includes('rain')) {
                    condition = 'Rain';
                } else if (weatherDesc.toLowerCase().includes('cloud')) {
                    condition = 'Clouds';
                } else if (weatherDesc.toLowerCase().includes('clear') || weatherDesc.toLowerCase().includes('sunny')) {
                    condition = 'Clear';
                }
                
                currentWeather = {
                    condition: condition,
                    description: weatherDesc,
                    temp: temp,
                    feelsLike: feelsLike,
                    location: 'Boston, MA'
                };
                
                updateWeatherDisplay();
                setWeatherEffect(condition);
                
            } catch (error) {
                console.error('Weather fetch failed:', error);
                currentWeather = {
                    condition: 'Clear',
                    description: 'Unable to fetch weather',
                    temp: '--',
                    feelsLike: '--',
                    location: 'Boston, MA'
                };
                updateWeatherDisplay();
            }
        }

        function updateWeatherDisplay() {
            if (!currentWeather) return;

            const iconMap = {
                'Clear': '☀️',
                'Clouds': '☁️',
                'Rain': '🌧️',
                'Drizzle': '🌦️',
                'Thunderstorm': '⛈️',
                'Snow': '❄️',
                'Mist': '🌫️',
                'Fog': '🌫️',
                'Haze': '🌫️'
            };

            const icon = iconMap[currentWeather.condition] || '☁️';
            document.getElementById('weatherIcon').textContent = icon;

            const weatherInfo = document.getElementById('weatherInfo');
            weatherInfo.innerHTML = `
                <div class="weather-info-title">${icon} ${currentWeather.description}</div>
                <div class="weather-info-detail">
                    ${currentWeather.location} • ${currentWeather.temp}°F (feels like ${currentWeather.feelsLike}°F)
                </div>
            `;
        }

        fetchWeather();
        setInterval(fetchWeather, 600000); // 10 minutes

        // ============================================================
        // PROTOCOL SELECTION
        // ============================================================

        function selectProtocol(protocol) {
            currentProtocol = protocol;
            
            document.querySelectorAll('.protocol-button').forEach(btn => {
                btn.classList.remove('active');
            });
            document.querySelector(`[data-protocol="${protocol}"]`).classList.add('active');
            
            const mode = protocol === 'auto' ? 'Intelligent Auto-Routing' : 
                        protocol === 'mcp' ? 'MCP Fast Path (forced)' : 
                        'A2A Multi-Agent (forced)';
            
            addSystemMessage(`Routing mode: ${mode}`);
        }

        // ============================================================
        // WEBSOCKET CONNECTION
        // ============================================================

        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.hostname}:${window.location.port}/ws`;
            
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log('WebSocket connected');
                document.getElementById('statusDot').classList.add('connected');
                document.getElementById('statusText').textContent = 'Connected';
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                console.log('Received:', data);

                if (data.type === 'response') {
                    addMessage('assistant', data.response);
                    updateInternals(data.metadata);
                } else if (data.type === 'error') {
                    addMessage('system', `Error: ${data.message}`);
                }

                document.getElementById('sendButton').disabled = false;
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                document.getElementById('statusDot').classList.remove('connected');
                document.getElementById('statusText').textContent = 'Error';
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                document.getElementById('statusDot').classList.remove('connected');
                document.getElementById('statusText').textContent = 'Disconnected';
                
                setTimeout(connectWebSocket, 3000);
            };
        }

        function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();

            if (!message || !ws || ws.readyState !== WebSocket.OPEN) {
                return;
            }

            addMessage('user', message);

            ws.send(JSON.stringify({
                message: message,
                force_protocol: currentProtocol
            }));

            input.value = '';
            document.getElementById('sendButton').disabled = true;

            updateInternals({
                path: 'processing',
                intent: 'analyzing...',
                confidence: 0
            });
        }

        function handleKeyPress(event) {
            if (event.key === 'Enter') {
                sendMessage();
            }
        }

        function addMessage(role, content) {
            const container = document.getElementById('messagesContainer');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${role}`;

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = content;

            messageDiv.appendChild(contentDiv);
            container.appendChild(messageDiv);

            container.scrollTop = container.scrollHeight;
        }

        function addSystemMessage(content) {
            addMessage('system', content);
        }

        function updateInternals(metadata) {
            const internalsContent = document.getElementById('internalsContent');
            
            if (metadata.path === 'processing') {
                internalsContent.innerHTML = `
                    <div class="info-block">
                        <div class="info-label">Status</div>
                        <div class="info-value">⏳ Processing query...</div>
                    </div>
                `;
                return;
            }

            const unified = metadata.unified_decision || {};
            const path = metadata.path || unified.path || 'unknown';
            const intent = unified.intent || 'unknown';
            const confidence = unified.confidence || 0;
            const latency = metadata.latency_ms || 0;
            const reasoning = unified.reasoning || 'No reasoning provided';
            const agents = metadata.agents_called || [];
            const manualOverride = unified.manual_override || false;
            const forceProtocol = unified.force_protocol || 'auto';
            const fallbackReason = metadata.fallback_reason || metadata.mcp_error || null;

            let badgeClass = 'mcp';
            let badgeText = 'MCP';
            if (path === 'a2a_fallback') {
                badgeClass = 'fallback';
                badgeText = 'A2A FALLBACK';
            } else if (path === 'a2a') {
                badgeClass = 'a2a';
                badgeText = 'A2A';
            } else if (path === 'shortcut') {
                badgeClass = 'shortcut';
                badgeText = 'SHORTCUT';
            }

            const latencyPercent = Math.min((latency / 3000) * 100, 100);

            internalsContent.innerHTML = `
                <div class="info-block">
                    <div class="info-label">Routing Path</div>
                    <div class="info-value">
                        <span class="badge ${badgeClass}">${badgeText}</span>
                        ${manualOverride ? '<span class="badge override">🔧 MANUAL OVERRIDE</span>' : ''}
                    </div>
                </div>

                ${(manualOverride && forceProtocol === 'mcp' && (path === 'a2a' || path === 'a2a_fallback')) ? `
                <div class="info-block">
                    <div class="info-label">Forced MCP Result</div>
                    <div class="info-value" style="color: #ffa07a;">
                        MCP was not used for execution; request ran via ${path.toUpperCase()}.
                        ${fallbackReason ? `<br>${fallbackReason}` : ''}
                    </div>
                </div>
                ` : ''}

                ${manualOverride ? `
                <div class="info-block">
                    <div class="info-label">Override Mode</div>
                    <div class="info-value" style="color: #ffd93d;">
                        User selected: ${forceProtocol.toUpperCase()}
                    </div>
                </div>
                ` : ''}

                <div class="info-block">
                    <div class="info-label">Intent Classification</div>
                    <div class="info-value">${intent} (${(confidence * 100).toFixed(0)}%)</div>
                </div>

                <div class="info-block">
                    <div class="info-label">Response Time</div>
                    <div class="info-value">${latency}ms</div>
                    <div class="latency-bar">
                        <div class="latency-fill" style="width: ${latencyPercent}%"></div>
                    </div>
                </div>

                <div class="info-block">
                    <div class="info-label">Routing Logic</div>
                    <div class="info-value" style="font-size: 12px; line-height: 1.6;">${reasoning}</div>
                </div>

                ${agents.length > 0 ? `
                <div class="info-block">
                    <div class="info-label">Agents Called (${agents.length})</div>
                    <ul class="agent-list">
                        ${agents.map(agent => `<li class="agent-item">→ ${agent}</li>`).join('')}
                    </ul>
                </div>
                ` : ''}
            `;
        }

        // Initialize
        connectWebSocket();
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
            force_protocol = data.get('force_protocol', 'auto')  # NEW: Get protocol override
            
            # Call Exchange Agent with protocol override
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.post(
                        f"{EXCHANGE_AGENT_URL}/chat",
                        json={
                            'query': message,
                            'conversation_id': conversation_id,
                            'force_protocol': force_protocol  # NEW: Pass to backend
                        },
                        timeout=30.0
                    )
                    response.raise_for_status()
                    result = response.json()

                    confidence_value = result.get('confidence', 0.0)
                    logger.info(f"Query: '{message}' | Confidence: {confidence_value} | Intent: {result.get('intent')} | Path: {result.get('path')} | Override: {force_protocol}")

                    # Send response back to client
                    await manager.send_message({
                        'type': 'response',
                        'response': result['response'],
                        'conversation_id': conversation_id,
                        'metadata': {
                            **result.get('metadata', {}),
                            'path': result.get('path'),
                            'latency_ms': result.get('latency_ms')
                        }
                    }, websocket)
                    
                except httpx.HTTPError as e:
                    logger.error(f"Error calling exchange agent: {e}")
                    await manager.send_message({
                        'type': 'error',
                        'message': 'Failed to process message. Please try again.'
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
