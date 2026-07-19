// Voice Channel Console Frontend JavaScript

// State management
let currentDigestText = "";
let isPlaying = false;
let currentUtterance = null;
let speechRate = 1.0;
let lastMessageTimestamp = null;
let recognition = null;
let isListening = false;
let activeRoomId = null;
let roomsList = [];

// DOM Elements
const rcStatusChip = document.getElementById("rc-status-chip");
const ttsStatusChip = document.getElementById("tts-status-chip");
const visualizer = document.getElementById("visualizer");
const digestContent = document.getElementById("digest-content");
const btnGenerateDigest = document.getElementById("btn-generate-digest");
const btnPlayPause = document.getElementById("btn-play-pause");
const playIcon = document.getElementById("play-icon");
const btnStop = document.getElementById("btn-stop");
const speedRange = document.getElementById("speed-range");
const speedVal = document.getElementById("speed-val");
const transcriptFeed = document.getElementById("transcript-feed");
const commandInput = document.getElementById("command-input");
const btnMic = document.getElementById("btn-mic");
const confirmationGate = document.getElementById("confirmation-gate");
const btnPreSend = document.getElementById("btn-pre-send");
const btnCancelSend = document.getElementById("btn-cancel-send");
const btnConfirmSend = document.getElementById("btn-confirm-send");
const roomSelect = document.getElementById("room-select");
const statsBar = document.getElementById("stats-bar");
const digestSourcesContainer = document.getElementById("digest-sources-container");
const sourcesToggle = document.getElementById("sources-toggle");
const sourcesToggleText = document.getElementById("sources-toggle-text");
const sourcesArrow = document.getElementById("sources-arrow");
const sourcesList = document.getElementById("sources-list");

// New Three-Pane DOM Elements
const channelsListEl = document.getElementById("channels-list");
const channelSearchInput = document.getElementById("channel-search-input");
const currentRoomNameEl = document.getElementById("current-room-name");
const currentRoomStatusEl = document.getElementById("current-room-status");
const btnToggleNarrator = document.getElementById("btn-toggle-narrator");
const btnCloseNarrator = document.getElementById("btn-close-narrator");
const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
const narratorSidebar = document.getElementById("narrator-sidebar");
const channelsSidebar = document.getElementById("channels-sidebar");

// Initialize application
function init() {
    activeRoomId = localStorage.getItem("activeRoomId");

    initNarratorSidebarState();
    checkStatus();
    loadRooms().then(() => {
        loadHistory();
    });
    
    initSpeechSynthesis();
    initSpeechRecognition();
    
    // Set up polling for status and transcript history every 5 seconds
    setInterval(checkStatus, 5000);
    setInterval(loadHistory, 5000);
    
    // Bind Event Listeners
    btnGenerateDigest.addEventListener("click", handleGenerateDigest);
    btnPlayPause.addEventListener("click", handlePlayPause);
    btnStop.addEventListener("click", handleStop);
    speedRange.addEventListener("input", handleSpeedChange);
    btnMic.addEventListener("click", toggleSpeechInput);
    btnPreSend.addEventListener("click", showConfirmation);
    btnCancelSend.addEventListener("click", hideConfirmation);
    btnConfirmSend.addEventListener("click", sendDraftedMessage);
    
    if (roomSelect) {
        roomSelect.addEventListener("change", handleRoomChange);
    }
    
    // Channel Search Filter
    if (channelSearchInput) {
        channelSearchInput.addEventListener("input", (e) => {
            renderChannelsList(e.target.value);
        });
    }

    // Narrator Sidebar Toggles
    if (btnToggleNarrator) {
        btnToggleNarrator.addEventListener("click", toggleNarratorSidebar);
    }
    if (btnCloseNarrator) {
        btnCloseNarrator.addEventListener("click", closeNarratorSidebar);
    }

    // Mobile Sidebar Toggle
    if (btnToggleSidebar) {
        btnToggleSidebar.addEventListener("click", () => {
            channelsSidebar.classList.toggle("mobile-open");
        });
    }
    
    // Setup sources toggle listener
    if (sourcesToggle) {
        sourcesToggle.addEventListener("click", () => {
            const isHidden = sourcesList.classList.contains("hidden");
            if (isHidden) {
                sourcesList.classList.remove("hidden");
                sourcesArrow.classList.add("rotated");
            } else {
                sourcesList.classList.add("hidden");
                sourcesArrow.classList.remove("rotated");
            }
        });
    }
    
    // Agent buttons click handlers
    document.querySelectorAll(".agent-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const agentName = btn.dataset.agent;
            commandInput.value = `@${agentName} ` + commandInput.value;
            commandInput.focus();
        });
    });
    
    // Quick Tag buttons click handlers
    document.querySelectorAll(".tag-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            commandInput.value = btn.dataset.cmd;
            commandInput.focus();
        });
    });
}

function initNarratorSidebarState() {
    const isOpen = localStorage.getItem("narratorOpen") !== "false";
    if (isOpen) {
        narratorSidebar.classList.remove("collapsed");
        if (btnToggleNarrator) btnToggleNarrator.classList.add("active");
    } else {
        narratorSidebar.classList.add("collapsed");
        if (btnToggleNarrator) btnToggleNarrator.classList.remove("active");
    }
}

function toggleNarratorSidebar() {
    const isCollapsed = narratorSidebar.classList.contains("collapsed");
    if (isCollapsed) {
        narratorSidebar.classList.remove("collapsed");
        if (btnToggleNarrator) btnToggleNarrator.classList.add("active");
        localStorage.setItem("narratorOpen", "true");
    } else {
        closeNarratorSidebar();
    }
}

function closeNarratorSidebar() {
    narratorSidebar.classList.add("collapsed");
    if (btnToggleNarrator) btnToggleNarrator.classList.remove("active");
    localStorage.setItem("narratorOpen", "false");
}

// Run immediately if DOM is already ready, otherwise wait
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}

// 1. Connection and Status Monitoring
async function checkStatus() {
    try {
        const response = await fetch("/api/status");
        if (!response.ok) throw new Error("HTTP error " + response.status);
        const data = await response.json();
        
        // Update Rocket.Chat status chip
        const rc = data.rocket_chat;
        rcStatusChip.className = "status-chip";
        
        if (rc.status === "connected") {
            rcStatusChip.classList.add("connected");
            rcStatusChip.querySelector(".status-label").innerText = `Rocket.Chat: Online`;
        } else if (rc.status === "auth_failed") {
            rcStatusChip.classList.add("error");
            rcStatusChip.querySelector(".status-label").innerText = `Rocket.Chat: Auth Error`;
        } else {
            rcStatusChip.classList.add("error");
            rcStatusChip.querySelector(".status-label").innerText = `Rocket.Chat: Offline`;
        }
    } catch (err) {
        console.error("Status check failed:", err);
        rcStatusChip.className = "status-chip error";
        rcStatusChip.querySelector(".status-label").innerText = `Rocket.Chat: Offline`;
    }
}

// 1.5. Room Selector Management
async function loadRooms() {
    try {
        const response = await fetch("/api/rooms");
        if (!response.ok) throw new Error("HTTP error " + response.status);
        const data = await response.json();
        
        if (data.success && data.rooms && data.rooms.length > 0) {
            roomsList = data.rooms;
            
            // Re-render select options
            if (roomSelect) {
                roomSelect.innerHTML = roomsList.map(room => {
                    return `<option value="${room.id}">${escapeHTML(room.name)}</option>`;
                }).join("");
            }
            
            // Determine active room ID
            if (activeRoomId) {
                // Check if stored room ID is still valid
                const roomExists = roomsList.some(r => r.id === activeRoomId);
                if (roomExists) {
                    if (roomSelect) roomSelect.value = activeRoomId;
                } else {
                    activeRoomId = roomsList[0]?.id;
                    if (roomSelect) roomSelect.value = activeRoomId;
                    localStorage.setItem("activeRoomId", activeRoomId);
                }
            } else {
                // Default to 'production_repo' if present, otherwise 'voice_channel', otherwise first room
                const prodRepoRoom = roomsList.find(r => r.name === "production_repo");
                const voiceChanRoom = roomsList.find(r => r.name === "voice_channel");
                
                if (prodRepoRoom) {
                    activeRoomId = prodRepoRoom.id;
                } else if (voiceChanRoom) {
                    activeRoomId = voiceChanRoom.id;
                } else {
                    activeRoomId = roomsList[0]?.id;
                }
                
                if (roomSelect) roomSelect.value = activeRoomId;
                localStorage.setItem("activeRoomId", activeRoomId);
            }

            renderChannelsList();
            updateHeaderRoomInfo();
        } else {
            if (roomSelect) roomSelect.innerHTML = `<option value="">No rooms found</option>`;
            if (channelsListEl) channelsListEl.innerHTML = `<div class="empty-channels">No rooms found</div>`;
            showTranscriptError("No channels available from Rocket.Chat.");
        }
    } catch (err) {
        console.error("Failed to load rooms:", err);
        if (roomSelect) roomSelect.innerHTML = `<option value="error">Error loading rooms</option>`;
        showTranscriptError("Failed to load rooms: " + err.message);
    }
}

function selectRoom(roomId) {
    if (!roomId || roomId === activeRoomId) return;
    activeRoomId = roomId;
    if (roomSelect) roomSelect.value = roomId;
    localStorage.setItem("activeRoomId", activeRoomId);
    renderChannelsList(channelSearchInput ? channelSearchInput.value : "");
    updateHeaderRoomInfo();
    handleRoomChange();
}

function updateHeaderRoomInfo() {
    const activeRoom = roomsList.find(r => r.id === activeRoomId);
    if (activeRoom && currentRoomNameEl) {
        currentRoomNameEl.innerText = `#${activeRoom.name}`;
    }
}

function renderChannelsList(filterText = "") {
    if (!channelsListEl) return;
    
    const term = (filterText || "").trim().toLowerCase();
    const filtered = roomsList.filter(r => (r.name || "").toLowerCase().includes(term));
    
    if (filtered.length === 0) {
        channelsListEl.innerHTML = `
            <div class="empty-channels">
                <span class="material-symbols-rounded">search_off</span>
                <span>No channels found</span>
            </div>
        `;
        return;
    }
    
    channelsListEl.innerHTML = filtered.map(room => {
        const isActive = room.id === activeRoomId;
        const activeClass = isActive ? "active" : "";
        return `
            <div class="channel-item ${activeClass}" data-room-id="${room.id}">
                <span class="material-symbols-rounded channel-icon">hashtag</span>
                <div class="channel-info">
                    <span class="channel-name">${escapeHTML(room.name)}</span>
                </div>
            </div>
        `;
    }).join("");
    
    channelsListEl.querySelectorAll(".channel-item").forEach(item => {
        item.addEventListener("click", () => {
            const rid = item.dataset.roomId;
            selectRoom(rid);
            if (channelsSidebar) channelsSidebar.classList.remove("mobile-open");
        });
    });
}

function handleRoomChange() {
    if (roomSelect) activeRoomId = roomSelect.value;
    localStorage.setItem("activeRoomId", activeRoomId);
    renderChannelsList(channelSearchInput ? channelSearchInput.value : "");
    updateHeaderRoomInfo();
    
    // Clear transcript UI and state
    lastMessageTimestamp = null;
    transcriptFeed.innerHTML = `
        <div class="loading-state">
            <span class="material-symbols-rounded spinning">progress_activity</span>
            <p>Loading updates for selected room...</p>
        </div>
    `;
    
    // Clear digest Narrator UI
    currentDigestText = "";
    digestContent.innerText = 'No digest loaded. Click "Generate Digest" below to fetch the latest updates from the channel and read them aloud.';
    handleStop();
    
    // Reload
    loadHistory();
}

// 2. Transcript Management
function showTranscriptError(message) {
    transcriptFeed.innerHTML = `
        <div class="empty-state">
            <span class="material-symbols-rounded">error</span>
            <p>${escapeHTML(message)}</p>
        </div>
    `;
}

async function loadHistory() {
    if (!activeRoomId || activeRoomId === "loading" || activeRoomId === "error") return;
    try {
        const response = await fetch(`/api/history?roomId=${encodeURIComponent(activeRoomId)}&count=30`);
        if (!response.ok) throw new Error("HTTP error " + response.status);
        const data = await response.json();
        
        if (data.success && data.messages) {
            renderTranscript(data.messages);
            if (data.stats) {
                renderStats(data.stats);
            } else {
                statsBar.style.display = "none";
            }
        } else {
            showTranscriptError(data.detail || "Failed to load channel history.");
        }
    } catch (err) {
        console.error("Failed to load transcript history:", err);
        showTranscriptError("Failed to load transcript: " + err.message);
    }
}

function renderTranscript(messages) {
    if (messages.length === 0) {
        transcriptFeed.innerHTML = `
            <div class="empty-state">
                <span class="material-symbols-rounded">forum</span>
                <p>No messages in this channel yet.</p>
            </div>
        `;
        return;
    }
    
    // Check if we have new messages since last render to trigger auto-scroll
    let shouldScroll = false;
    if (messages.length > 0) {
        const latestMsg = messages[messages.length - 1];
        if (lastMessageTimestamp !== latestMsg.timestamp) {
            lastMessageTimestamp = latestMsg.timestamp;
            shouldScroll = true;
        }
    }
    
    // Generate HTML for messages
    transcriptFeed.innerHTML = messages.map(msg => {
        const date = new Date(msg.timestamp);
        const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        
        // Handle Lane A: System Events
        if (msg.lane === 'system') {
            const kind = msg.event?.kind || 'other';
            return `
                <div class="system-event-chip ${kind}" data-id="${msg.id}">
                    <span class="material-symbols-rounded system-icon">${getSystemIcon(kind)}</span>
                    <span class="system-text">${getSystemText(msg)}</span>
                    <span class="system-time">${timeStr}</span>
                </div>
            `;
        }
        
        // Handle Lane B/C: Agent and User Messages
        const laneClass = msg.lane === 'user' ? 'user' : 'agent';
        const cardClass = `chat-msg ${laneClass}`;
        
        // Relayed agent name resolution
        let displayName = msg.name || msg.username;
        if (msg.event?.kind === 'agent_response' && msg.event?.agent) {
            const agentName = msg.event.agent;
            displayName = agentName.charAt(0).toUpperCase() + agentName.slice(1);
        }
        
        // Response time badge
        let respTimeBadge = '';
        if (msg.event?.response_time_seconds !== undefined && msg.event?.response_time_seconds !== null) {
            const formatted = formatDuration(msg.event.response_time_seconds);
            respTimeBadge = `<span class="response-time-badge">${formatted} response</span>`;
        }
        
        return `
            <div class="${cardClass}" data-id="${msg.id}">
                <div class="chat-header">
                    <span class="chat-author">${escapeHTML(displayName)}</span>
                    ${respTimeBadge}
                    <span class="chat-time">${timeStr}</span>
                </div>
                <div class="chat-body">${renderMarkdown(msg.text)}</div>
            </div>
        `;
    }).join("");
    
    // Auto-scroll to bottom if new messages arrived
    if (shouldScroll) {
        transcriptFeed.scrollTop = transcriptFeed.scrollHeight;
    }
}

function renderStats(stats) {
    if (!stats || Object.keys(stats).length === 0) {
        statsBar.style.display = "none";
        return;
    }
    
    const items = Object.entries(stats).map(([agent, data]) => {
        const displayName = agent.charAt(0).toUpperCase() + agent.slice(1);
        let details = [];
        
        if (data.runs > 0) {
            details.push(`${data.runs} run${data.runs > 1 ? 's' : ''}`);
            details.push(`avg ${formatDuration(data.avg_response_time)}`);
        }
        if (data.msg_count > 0) {
            details.push(`${data.msg_count} msg${data.msg_count > 1 ? 's' : ''}`);
        }
        
        const detailsStr = details.length > 0 ? ` (${details.join(" · ")})` : "";
        
        if (data.status === "working") {
            const elapsedStr = data.current_elapsed > 0 ? ` for ${formatDuration(data.current_elapsed)}` : "";
            return `
                <div class="stats-item working">
                    <span class="material-symbols-rounded spinning" style="font-size: 0.8rem; display: inline-block;">sync</span>
                    <span class="agent-name">@${displayName}</span>
                    <span class="stats-val">working${elapsedStr}</span>
                </div>
            `;
        } else {
            return `
                <div class="stats-item">
                    <span class="agent-name">@${displayName}</span>
                    <span class="stats-val">${detailsStr || 'idle'}</span>
                </div>
            `;
        }
    }).join("<span class='stats-divider'>|</span>");
    
    if (items.trim() === "") {
        statsBar.style.display = "none";
    } else {
        statsBar.innerHTML = items;
        statsBar.style.display = "flex";
    }
}

function escapeHTML(str) {
    if (!str) return "";
    return str.replace(/[&<>'"]/g, 
        tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
}

function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
        return null;
    }
    let sec = Math.max(0, Math.round(Number(seconds)));
    if (sec < 60) return `${sec}s`;
    const hours = Math.floor(sec / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    const rem = sec % 60;
    if (hours > 0) {
        return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
    }
    return rem > 0 ? `${minutes}m ${rem}s` : `${minutes}m`;
}

function getSystemIcon(kind) {
    switch (kind) {
        case 'routing': return 'alt_route';
        case 'heartbeat': return 'sync';
        case 'model_selected': return 'settings_suggest';
        case 'stopped': return 'stop_circle';
        case 'attachment': return 'attach_file';
        case 'membership': return 'group';
        case 'error': return 'report';
        case 'system_startup': return 'power_settings_new';
        default: return 'info';
    }
}

function getSystemText(msg) {
    const ev = msg.event || {};
    const kind = ev.kind;
    const agent = escapeHTML(ev.agent || 'Agent');
    
    switch (kind) {
        case 'routing': {
            const modelName = escapeHTML(ev.model?.name || 'unknown');
            const provider = escapeHTML(ev.model?.provider || 'unknown');
            const effort = escapeHTML(ev.model?.effort || 'low');
            let statusSuffix = '';
            if (ev.status === 'completed' && ev.response_time_seconds) {
                statusSuffix = ` <span class="routing-completed">(completed in ${formatDuration(ev.response_time_seconds)})</span>`;
            } else if (ev.status === 'stopped') {
                statusSuffix = ` <span class="routing-stopped">(stopped)</span>`;
            } else if (ev.status === 'failed') {
                statusSuffix = ` <span class="routing-failed">(failed)</span>`;
            } else if (ev.status === 'working') {
                statusSuffix = ` <span class="routing-working pulsing">(running...)</span>`;
            }
            return `Routing to <strong>@${agent}</strong> [${modelName} (${provider}) · ${effort} effort]${statusSuffix}`;
        }
            
        case 'heartbeat': {
            const timeFormatted = formatDuration(ev.elapsed_seconds) || 'active';
            return `<strong>@${agent}</strong> is working (${timeFormatted} elapsed)`;
        }
            
        case 'model_selected': {
            const mName = escapeHTML(ev.model?.name || 'unknown');
            const mProv = escapeHTML(ev.model?.provider || 'unknown');
            const mEff = ev.model?.effort ? escapeHTML(ev.model.effort) : null;
            const effortStr = mEff ? ` · ${mEff} effort` : '';
            return `Model for <strong>@${agent}</strong> → <code>${mName}</code> (${mProv}${effortStr})`;
        }
            
        case 'attachment': {
            const count = ev.count || 1;
            return `Downloaded ${count} attachment(s) to inbox`;
        }
            
        case 'stopped':
            return `<strong>@${agent}</strong> run stopped or cancelled`;
            
        case 'error': {
            let detail = (ev.raw_text || msg.text || '').replace(/\s+/g, ' ').trim();
            if (detail.length > 140) detail = detail.slice(0, 140) + '…';
            return `Error for <strong>@${agent}</strong>: ${escapeHTML(detail)}`;
        }
            
        case 'membership':
            return `Membership update: ${escapeHTML(msg.text || '')}`;
            
        case 'system_startup':
            return `Voice Console daemon connected`;
            
        default:
            return escapeHTML(msg.text || '');
    }
}

function renderMarkdown(text) {
    if (!text) return "";
    
    // Clean relayed agent prefix if it exists
    let cleanText = text;
    const relayPrefixMatch = text.match(/^\*\*@[a-zA-Z0-9_]+\*\*:\s*([\s\S]*)/);
    if (relayPrefixMatch) {
        cleanText = relayPrefixMatch[1];
    }
    
    if (typeof marked !== 'undefined') {
        try {
            marked.setOptions({
                gfm: true,
                breaks: true
            });
            const html = marked.parse(cleanText);
            // marked.parse is sync by default; if a Promise ever appears, fall back.
            if (typeof html === 'string') {
                return sanitizeHTML(html);
            }
        } catch (e) {
            console.error("Markdown parsing failed, falling back to plaintext:", e);
        }
    }
    
    // Fallback simple parsing
    let html = escapeHTML(cleanText);
    html = html.replace(/\*\*([^\*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*([^\*]+)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    return html.replace(/\n/g, '<br>');
}

function sanitizeHTML(html) {
    // DOM-based allowlist scrub for markdown output (trusted RC content, still defensive).
    const template = document.createElement('template');
    template.innerHTML = html;
    const blocked = new Set(['script', 'iframe', 'object', 'embed', 'form', 'link', 'meta', 'base']);
    const walk = (root) => {
        const nodes = Array.from(root.querySelectorAll('*'));
        for (const el of nodes) {
            const tag = el.tagName.toLowerCase();
            if (blocked.has(tag)) {
                el.remove();
                continue;
            }
            for (const attr of Array.from(el.attributes)) {
                const name = attr.name.toLowerCase();
                const value = (attr.value || '').trim().toLowerCase();
                if (name.startsWith('on') || value.startsWith('javascript:') || value.startsWith('data:text/html')) {
                    el.removeAttribute(attr.name);
                }
            }
        }
    };
    walk(template.content);
    return template.innerHTML;
}

// 3. Text-to-Speech (TTS) Narrator
function initSpeechSynthesis() {
    if (!('speechSynthesis' in window)) {
        console.error("Speech Synthesis is not supported in this browser.");
        ttsStatusChip.className = "status-chip error";
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Unsupported`;
        return;
    }
    
    // Warm up the engine
    window.speechSynthesis.cancel();
    
    ttsStatusChip.className = "status-chip active";
    ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Ready`;
}

async function handleGenerateDigest() {
    if (!activeRoomId) return;
    btnGenerateDigest.disabled = true;
    digestContent.innerHTML = "<em>Generating new digest from recent updates...</em>";
    
    try {
        // Fetch current messages
        const histResponse = await fetch(`/api/history?roomId=${activeRoomId}&count=20`);
        if (!histResponse.ok) throw new Error("Failed to fetch messages");
        const histData = await histResponse.json();
        
        if (!histData.success || !histData.messages || histData.messages.length === 0) {
            digestContent.innerText = "No messages available to summarize.";
            btnGenerateDigest.disabled = false;
            return;
        }
        
        // Generate digest from these messages
        const digestResponse = await fetch("/api/digest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
                messages: histData.messages,
                roomId: activeRoomId
            })
        });
        
        if (!digestResponse.ok) throw new Error("Failed to generate digest");
        const digestData = await digestResponse.json();
        
        currentDigestText = digestData.digest;
        digestContent.innerText = currentDigestText;
        
        // Render Auditable Sources List (Step 5)
        const sourceIds = digestData.included_message_ids || [];
        if (sourceIds.length > 0) {
            const sourceMsgs = histData.messages.filter(m => sourceIds.includes(m.id));
            
            sourcesToggleText.innerText = `Show Sources (${sourceMsgs.length})`;
            
            sourcesList.innerHTML = sourceMsgs.map(m => {
                const author = m.name || m.username;
                let textSnippet = m.text || "";
                if (textSnippet.length > 85) {
                    textSnippet = textSnippet.substring(0, 85) + "...";
                }
                return `
                    <div class="source-item" data-source-id="${m.id}">
                        <span class="source-author">@${escapeHTML(author)}:</span>
                        <span class="source-snippet">${escapeHTML(textSnippet)}</span>
                    </div>
                `;
            }).join("");
            
            // Add click listeners to source items
            sourcesList.querySelectorAll(".source-item").forEach(item => {
                item.addEventListener("click", () => {
                    const msgId = item.dataset.sourceId;
                    const msgElem = document.querySelector(`[data-id="${msgId}"]`);
                    if (msgElem) {
                        msgElem.scrollIntoView({ behavior: "smooth", block: "center" });
                        msgElem.classList.remove("highlight-pulse");
                        void msgElem.offsetWidth; // Trigger browser reflow for CSS keyframe animation restart
                        msgElem.classList.add("highlight-pulse");
                    }
                });
            });
            
            digestSourcesContainer.style.display = "block";
        } else {
            digestSourcesContainer.style.display = "none";
            sourcesList.innerHTML = "";
        }
        
        // Stop current speaking and play new digest
        handleStop();
        speakText(currentDigestText);
        
    } catch (err) {
        console.error("Digest generation failed:", err);
        digestContent.innerText = "Error generating digest. Please check console.";
    } finally {
        btnGenerateDigest.disabled = false;
    }
}

function speakText(text) {
    if (!text || !('speechSynthesis' in window)) return;
    
    // Cancel any ongoing speech
    window.speechSynthesis.cancel();
    
    currentUtterance = new SpeechSynthesisUtterance(text);
    currentUtterance.rate = speechRate;
    
    // Choose a high quality voice if available
    const voices = window.speechSynthesis.getVoices();
    // Prefer standard, natural sounding English voices (Google, Daniel, Samantha, etc.)
    const preferredVoice = voices.find(v => 
        (v.name.includes("Natural") || v.name.includes("Google") || v.name.includes("Samantha")) && v.lang.startsWith("en")
    ) || voices.find(v => v.lang.startsWith("en"));
    
    if (preferredVoice) {
        currentUtterance.voice = preferredVoice;
    }
    
    currentUtterance.onstart = () => {
        isPlaying = true;
        visualizer.classList.add("playing");
        playIcon.textContent = "pause";
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Speaking`;
    };
    
    currentUtterance.onend = () => {
        isPlaying = false;
        visualizer.classList.remove("playing");
        playIcon.textContent = "play_arrow";
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Ready`;
        currentUtterance = null;
    };
    
    currentUtterance.onerror = (e) => {
        console.error("SpeechSynthesis error:", e);
        isPlaying = false;
        visualizer.classList.remove("playing");
        playIcon.textContent = "play_arrow";
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Error`;
        currentUtterance = null;
    };
    
    window.speechSynthesis.speak(currentUtterance);
}

function handlePlayPause() {
    if (!currentDigestText) {
        // If no digest loaded, generate one first
        handleGenerateDigest();
        return;
    }
    
    if (window.speechSynthesis.speaking) {
        if (window.speechSynthesis.paused) {
            window.speechSynthesis.resume();
            isPlaying = true;
            visualizer.classList.add("playing");
            playIcon.textContent = "pause";
        } else {
            window.speechSynthesis.pause();
            isPlaying = false;
            visualizer.classList.remove("playing");
            playIcon.textContent = "play_arrow";
        }
    } else {
        // Play from scratch
        speakText(currentDigestText);
    }
}

function handleStop() {
    window.speechSynthesis.cancel();
    isPlaying = false;
    visualizer.classList.remove("playing");
    playIcon.textContent = "play_arrow";
    ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Ready`;
}

function handleSpeedChange() {
    speechRate = parseFloat(speedRange.value);
    speedVal.innerText = `${speechRate.toFixed(1)}x`;
    
    // If speaking, restart from the beginning (or let the rate change take effect for the next utterance)
    if (window.speechSynthesis.speaking && currentUtterance) {
        // For browsers that support changing rate mid-speech
        currentUtterance.rate = speechRate;
        // In some browsers, we must cancel and restart to apply rate changes:
        if (isPlaying) {
            speakText(currentDigestText);
        }
    }
}

// 4. Speech-to-Text (STT) Recognition
function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        btnMic.disabled = true;
        btnMic.title = "Voice input is not supported in this browser.";
        return;
    }
    
    recognition = new SpeechRecognition();
    recognition.continuous = false; // Stop listening when user stops speaking
    recognition.interimResults = false;
    recognition.lang = "en-US";
    
    recognition.onstart = () => {
        isListening = true;
        btnMic.classList.remove("btn-secondary");
        btnMic.classList.add("btn-danger", "pulsing");
        btnMic.querySelector(".material-symbols-rounded").textContent = "mic_off";
    };
    
    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        if (commandInput.value) {
            commandInput.value += " " + transcript;
        } else {
            commandInput.value = transcript;
        }
        commandInput.focus();
    };
    
    recognition.onerror = (e) => {
        console.error("Speech Recognition error:", e);
        stopListening();
    };
    
    recognition.onend = () => {
        stopListening();
    };
}

function toggleSpeechInput() {
    if (!recognition) return;
    
    if (isListening) {
        recognition.stop();
        stopListening();
    } else {
        // Pause narrator if it is currently reading
        if (window.speechSynthesis.speaking && !window.speechSynthesis.paused) {
            window.speechSynthesis.pause();
            isPlaying = false;
            visualizer.classList.remove("playing");
            playIcon.textContent = "play_arrow";
        }
        recognition.start();
    }
}

function stopListening() {
    isListening = false;
    btnMic.classList.remove("btn-danger", "pulsing");
    btnMic.classList.add("btn-secondary");
    btnMic.querySelector(".material-symbols-rounded").textContent = "mic";
}

// 5. Message Composer & Confirmation Workflows
function showConfirmation() {
    const text = commandInput.value.trim();
    if (!text) return;
    
    confirmationGate.classList.remove("hidden");
    btnPreSend.classList.add("hidden");
}

function hideConfirmation() {
    confirmationGate.classList.add("hidden");
    btnPreSend.classList.remove("hidden");
}

async function sendDraftedMessage() {
    const text = commandInput.value.trim();
    if (!text || !activeRoomId) return;
    
    btnConfirmSend.disabled = true;
    
    try {
        const response = await fetch("/api/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ roomId: activeRoomId, text: text })
        });
        
        if (!response.ok) throw new Error("Failed to post message");
        const data = await response.json();
        
        if (data.success) {
            commandInput.value = "";
            hideConfirmation();
            loadHistory(); // Reload history immediately to see the new message
        } else {
            alert("Error sending message to Rocket.Chat");
        }
    } catch (err) {
        console.error("Post message failed:", err);
        alert("Failed to send message: " + err.message);
    } finally {
        btnConfirmSend.disabled = false;
    }
}
