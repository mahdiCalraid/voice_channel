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
let liveRequestSeq = 0;
let loadOlderRequestSeq = 0;
let confirmationTargetRoomId = null;
let confirmationTargetText = null;
let confirmationNonce = null;
let gatewayConfirmationSnapshot = null;
let roomHistoryStates = {};
const historyState = window.VoiceChannelHistoryState;

if (!historyState) {
    throw new Error("history_state.js must load before index.js");
}

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
const assistantStatus = document.getElementById("assistant-status");

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

// Settings Management (U-01, U-02, U-03, U-05)
const DEFAULT_SETTINGS = {
    settingsVersion: 2,
    fontSize: "medium",
    historyLimit: 20,
    defaultAgent: "codex",
    autoNarrate: true
};

function getSettings() {
    try {
        const raw = localStorage.getItem("vc_settings");
        if (!raw) return { ...DEFAULT_SETTINGS };
        const stored = JSON.parse(raw);
        // U-03 originally shipped disabled by default. Migrate that placeholder
        // setting once so the automatic narrator + suggestion loop is actually on.
        if (!stored.settingsVersion || stored.settingsVersion < 2) {
            stored.autoNarrate = true;
            stored.settingsVersion = 2;
        }
        return { ...DEFAULT_SETTINGS, ...stored };
    } catch (e) {
        return { ...DEFAULT_SETTINGS };
    }
}

function saveSettings(settings) {
    try {
        localStorage.setItem("vc_settings", JSON.stringify(settings));
        applySettings(settings);
    } catch (e) {
        console.error("Failed to save settings:", e);
    }
}

function applySettings(settings = getSettings()) {
    document.body.classList.remove("font-normal", "font-medium", "font-large", "font-xlarge");
    document.body.classList.add("font-" + (settings.fontSize || "medium"));
    if (settings.autoNarrate) {
        setAssistantStatus("Automatic: waiting", "on");
    } else {
        setAssistantStatus("Automatic: off", "off");
    }
}

function setAssistantStatus(text, state = "on") {
    if (!assistantStatus) return;
    assistantStatus.className = "assistant-status is-" + state;
    assistantStatus.innerHTML = '<span class="assistant-status-dot"></span>' + escapeHTML(text);
}

function emptyNarratorText() {
    return getSettings().autoNarrate
        ? "Automatic assistance is on. Waiting for the next real agent response; you can also generate a digest manually."
        : 'Automatic assistance is off. Click "Generate Digest" to create narration and a next-message draft manually.';
}

function initSettingsModal() {
    const btnOpen = document.getElementById("btn-open-settings");
    const btnClose = document.getElementById("btn-close-settings");
    const btnSave = document.getElementById("btn-save-settings");
    const modal = document.getElementById("settings-modal");
    
    const selFontSize = document.getElementById("setting-font-size");
    const selHistoryLimit = document.getElementById("setting-history-limit");
    const selDefaultAgent = document.getElementById("setting-default-agent");
    const chkAutoNarrate = document.getElementById("setting-auto-narrate");
    
    if (!btnOpen || !modal) return;
    
    btnOpen.addEventListener("click", () => {
        const settings = getSettings();
        if (selFontSize) selFontSize.value = settings.fontSize || "medium";
        if (selHistoryLimit) selHistoryLimit.value = String(settings.historyLimit || 20);
        if (selDefaultAgent) selDefaultAgent.value = settings.defaultAgent || "codex";
        if (chkAutoNarrate) chkAutoNarrate.checked = !!settings.autoNarrate;
        
        modal.classList.remove("hidden");
    });
    
    const closeModal = () => modal.classList.add("hidden");
    if (btnClose) btnClose.addEventListener("click", closeModal);
    
    if (btnSave) {
        btnSave.addEventListener("click", () => {
            const updated = {
                settingsVersion: 2,
                fontSize: selFontSize ? selFontSize.value : "medium",
                historyLimit: selHistoryLimit ? parseInt(selHistoryLimit.value, 10) : 20,
                defaultAgent: selDefaultAgent ? selDefaultAgent.value : "codex",
                autoNarrate: chkAutoNarrate ? chkAutoNarrate.checked : false
            };
            saveSettings(updated);
            closeModal();
        });
    }
}

// Initialize application
function init() {
    applySettings();
    initSettingsModal();
    activeRoomId = localStorage.getItem("activeRoomId");

    initNarratorSidebarState();
    checkStatus();
    loadRooms().then(() => {
        if (activeRoomId) {
            restoreRoomUIData(activeRoomId);
            renderTranscriptFromState(activeRoomId);
        }
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
    
    if (commandInput) {
        commandInput.addEventListener("input", (e) => {
            if (activeRoomId) {
                const state = getRoomState(activeRoomId);
                state.draftText = e.target.value;
                if (state.lastInsertedSuggestion && e.target.value !== state.lastInsertedSuggestion) {
                    state.lastInsertedSuggestion = null;
                }
                if (e.target.value.trim()) {
                    localStorage.setItem("vc_draft_" + activeRoomId, e.target.value);
                } else {
                    localStorage.removeItem("vc_draft_" + activeRoomId);
                }
            }
        });
    }
    
    if (roomSelect) {
        roomSelect.addEventListener("change", (e) => {
            selectRoom(e.target.value);
        });
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
        if (channelsListEl) {
            channelsListEl.innerHTML = `
                <div class="empty-channels">
                    <span class="material-symbols-rounded">error</span>
                    <span>Failed to load channels</span>
                </div>
            `;
        }
        showTranscriptError("Failed to load rooms: " + err.message);
    }
}

function saveRoomUIData(roomId) {
    if (!roomId) return;
    const state = getRoomState(roomId);
    if (commandInput) {
        state.draftText = commandInput.value;
        if (commandInput.value.trim()) {
            localStorage.setItem("vc_draft_" + roomId, commandInput.value);
        } else {
            localStorage.removeItem("vc_draft_" + roomId);
        }
    }
    state.digestText = currentDigestText;
    state.digestSourcesHtml = sourcesList ? sourcesList.innerHTML : "";
    state.digestSourcesVisible = digestSourcesContainer ? (digestSourcesContainer.style.display === "block") : false;
    state.sourcesToggleText = sourcesToggleText ? sourcesToggleText.innerText : "";
    state.sourcesListVisible = sourcesList ? !sourcesList.classList.contains("hidden") : false;
    state.sourcesArrowRotated = sourcesArrow ? sourcesArrow.classList.contains("rotated") : false;
    state.digestLoading = btnGenerateDigest ? btnGenerateDigest.disabled : false;
    
    if (statsBar) {
        state.statsHtml = statsBar.innerHTML;
        state.statsVisible = statsBar.style.display !== "none";
    }
    if (transcriptFeed) {
        state.scrollTop = transcriptFeed.scrollTop;
    }
}

function restoreRoomUIData(roomId) {
    if (!roomId) return;
    const state = getRoomState(roomId);
    
    if (state.draftText === undefined || state.draftText === null) {
        state.draftText = localStorage.getItem("vc_draft_" + roomId) || "";
    }
    
    if (commandInput) {
        commandInput.value = state.draftText || "";
    }
    
    currentDigestText = state.digestText || "";
    if (digestContent) {
        if (currentDigestText) {
            digestContent.innerText = currentDigestText;
        } else {
            digestContent.innerText = emptyNarratorText();
        }
    }
    
    if (sourcesList) {
        sourcesList.innerHTML = state.digestSourcesHtml || "";
        if (state.sourcesListVisible) {
            sourcesList.classList.remove("hidden");
        } else {
            sourcesList.classList.add("hidden");
        }
        bindSourceItemHandlers();
    }
    
    if (digestSourcesContainer) {
        digestSourcesContainer.style.display = state.digestSourcesVisible ? "block" : "none";
    }
    
    if (sourcesToggleText) {
        sourcesToggleText.innerText = state.sourcesToggleText || "Show Sources (0)";
    }
    
    if (sourcesArrow) {
        if (state.sourcesArrowRotated) {
            sourcesArrow.classList.add("rotated");
        } else {
            sourcesArrow.classList.remove("rotated");
        }
    }
    
    if (btnGenerateDigest) {
        btnGenerateDigest.disabled = state.digestLoading || false;
    }
    
    if (statsBar) {
        statsBar.innerHTML = state.statsHtml || "";
        statsBar.style.display = state.statsVisible ? "flex" : "none";
    }
    
    if (state.scrollTop !== undefined && state.scrollTop !== null) {
        state.savedScrollTop = state.scrollTop;
    } else {
        state.savedScrollTop = null;
    }
}

function selectRoom(roomId) {
    if (!roomId) return;
    if (roomId === activeRoomId) return;
    const oldRoomId = activeRoomId;
    
    if (oldRoomId && oldRoomId !== roomId) {
        saveRoomUIData(oldRoomId);
    }
    
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
    
    // Sort channels by most recent activity timestamp descending (U-01)
    const sortedRooms = [...roomsList].sort((a, b) => {
        const timeA = new Date(a._updatedAt || a.lm || a.updatedAt || 0).getTime();
        const timeB = new Date(b._updatedAt || b.lm || b.updatedAt || 0).getTime();
        return timeB - timeA;
    });
    
    const filtered = sortedRooms.filter(r => (r.name || "").toLowerCase().includes(term));
    
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
            <div class="channel-item ${activeClass}" data-room-id="${room.id}" role="button" tabindex="0">
                <span class="material-symbols-rounded channel-icon">tag</span>
                <div class="channel-info">
                    <span class="channel-name">${escapeHTML(room.name)}</span>
                </div>
            </div>
        `;
    }).join("");
    
    channelsListEl.querySelectorAll(".channel-item").forEach(item => {
        const handleSelect = () => {
            const rid = item.dataset.roomId;
            selectRoom(rid);
            if (channelsSidebar) channelsSidebar.classList.remove("mobile-open");
        };
        item.addEventListener("click", handleSelect);
        item.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                handleSelect();
            }
        });
    });
}

function handleRoomChange() {
    // Always hide/cancel any pending confirmation gate on room switch
    hideConfirmation();
    
    // Restore UI state of the new active room
    restoreRoomUIData(activeRoomId);
    
    // Clear lastMessageTimestamp so renderTranscript updates scroll properly
    lastMessageTimestamp = null;
    
    const state = getRoomState(activeRoomId);
    if (state.orderedIds.length > 0) {
        // If we have cached messages, render them immediately!
        renderTranscriptFromState(activeRoomId);
    } else {
        // Otherwise, display loading state
        transcriptFeed.innerHTML = `
            <div class="loading-state">
                <span class="material-symbols-rounded spinning">progress_activity</span>
                <p>Loading updates for selected room...</p>
            </div>
        `;
    }
    
    handleStop();
    
    // Reload history to get latest updates
    loadHistory();
}

// 2. Transcript Management
function getRoomState(roomId) {
    if (!roomHistoryStates[roomId]) {
        roomHistoryStates[roomId] = historyState.createRoomState();
    }
    return roomHistoryStates[roomId];
}

function bindSourceItemHandlers() {
    if (!sourcesList) return;
    sourcesList.querySelectorAll(".source-item").forEach(item => {
        item.addEventListener("click", () => {
            const msgId = item.dataset.sourceId;
            const msgElem = document.querySelector(`[data-id="${msgId}"]`);
            if (!msgElem) return;

            msgElem.scrollIntoView({ behavior: "smooth", block: "center" });
            msgElem.classList.remove("highlight-pulse");
            void msgElem.offsetWidth;
            msgElem.classList.add("highlight-pulse");
        });
    });
}

function renderRecentStats(stats) {
    if (stats) {
        renderStats(stats);
    } else {
        statsBar.style.display = "none";
    }
}

function flushPendingLatest(roomId, state) {
    if (roomId !== activeRoomId || !state.pendingLatest) return;

    const pending = state.pendingLatest;
    state.pendingLatest = null;
    historyState.applyLatestPage(state, pending.messages, pending.has_more);
    renderTranscriptFromState(roomId);
    renderRecentStats(pending.stats);
}

async function handleLoadOlder() {
    if (!activeRoomId || activeRoomId === "loading" || activeRoomId === "error") return;

    const state = getRoomState(activeRoomId);
    if (state.loadingOlder || !state.hasMoreOlder) return;

    const targetRoomId = activeRoomId;
    const currentSeq = ++loadOlderRequestSeq;
    state.loadingOlder = true;
    renderTranscriptFromState(targetRoomId);

    // Record scroll metrics before rendering prepended items
    const oldScrollHeight = transcriptFeed.scrollHeight;
    const oldScrollTop = transcriptFeed.scrollTop;

    try {
        const url = `/api/history?roomId=${encodeURIComponent(targetRoomId)}&count=30&before=${encodeURIComponent(state.oldestCursor)}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error("HTTP error " + response.status);
        const data = await response.json();

        if (targetRoomId !== activeRoomId || currentSeq !== loadOlderRequestSeq) return;

        if (data.success && data.messages) {
            state.loadingOlder = false;
            historyState.applyOlderPage(
                state,
                data.messages,
                data.has_more,
                data.next_before
            );

            renderTranscriptFromState(targetRoomId);

            // Restore scroll position
            const newScrollHeight = transcriptFeed.scrollHeight;
            transcriptFeed.scrollTop = historyState.restoredPrependScrollTop(
                oldScrollHeight,
                oldScrollTop,
                newScrollHeight
            );
            // Apply a poll that arrived during the prepend only after the reader's
            // original viewport has been restored.
            flushPendingLatest(targetRoomId, state);
        } else {
            state.loadingOlder = false;
            renderTranscriptFromState(targetRoomId);
            console.error("Failed to load older history:", data.detail);
        }
    } catch (err) {
        if (targetRoomId === activeRoomId && currentSeq === loadOlderRequestSeq) {
            console.error("Failed to load older history:", err);
        }
    } finally {
        // A room switch must release its old state's loading flag as well.
        const wasLoading = state.loadingOlder;
        state.loadingOlder = false;
        if (wasLoading && targetRoomId === activeRoomId && currentSeq === loadOlderRequestSeq) {
            renderTranscriptFromState(targetRoomId);
            flushPendingLatest(targetRoomId, state);
        }
    }
}

// Bind to window so it is accessible from inline onclick attribute
window.handleLoadOlder = handleLoadOlder;

function showTranscriptError(message) {
    transcriptFeed.innerHTML = `
        <div class="empty-state">
            <span class="material-symbols-rounded">signal_wifi_off</span>
            <p>${escapeHTML(message)}</p>
            <button class="btn btn-secondary retry-btn" onclick="loadHistory()">
                <span class="material-symbols-rounded">refresh</span> Retry Connection
            </button>
        </div>
    `;
}

function handleTranscriptError(targetRoomId, message) {
    const state = getRoomState(targetRoomId);
    // If the room already has rendered/cached messages, preserve them on screen and update status chip
    if (state && state.orderedIds.length > 0) {
        if (rcStatusChip) {
            rcStatusChip.className = "status-chip error";
            const label = rcStatusChip.querySelector(".status-label");
            if (label) label.innerText = "Rocket.Chat: Reconnecting...";
        }
    } else {
        showTranscriptError(message);
    }
}

function isRealAgentResponse(message) {
    return Boolean(
        message
        && message.id
        && message.lane === "agent"
        && message.event
        && message.event.kind === "agent_response"
    );
}

function getRoomName(roomId) {
    const room = roomsList.find(item => item.id === roomId);
    return room ? room.name : null;
}

function applySuggestedDraft(targetRoomId, suggestedMessage) {
    const draft = (suggestedMessage || "").trim();
    if (!draft) return false;

    const state = getRoomState(targetRoomId);
    const currentValue = targetRoomId === activeRoomId && commandInput
        ? commandInput.value
        : (state.draftText || "");
    const composerIsLocked = Boolean(
        targetRoomId === activeRoomId
        && commandInput
        && commandInput.readOnly
    );
    const canReplace = !composerIsLocked && (
        !currentValue.trim() || currentValue === state.lastInsertedSuggestion
    );

    state.suggestedDraft = draft;
    if (!canReplace) {
        return false;
    }

    state.draftText = draft;
    state.lastInsertedSuggestion = draft;
    localStorage.setItem("vc_draft_" + targetRoomId, draft);
    if (targetRoomId === activeRoomId && commandInput && !commandInput.readOnly) {
        commandInput.value = draft;
    }
    return true;
}

function checkForAutoNarrate(targetRoomId, newRealResponses) {
    const settings = getSettings();
    if (!settings.autoNarrate) return;
    if (!newRealResponses || newRealResponses.length === 0) return;

    const state = getRoomState(targetRoomId);
    const sorted = [...newRealResponses].sort(
        (a, b) => new Date(a.timestamp || 0).getTime() - new Date(b.timestamp || 0).getTime()
    );
    const newestMsg = sorted[sorted.length - 1];
    if (!isRealAgentResponse(newestMsg) || newestMsg.id === state.lastAssistedId) return;
    if (state.failedAssistantId === newestMsg.id && Date.now() < (state.assistantRetryAt || 0)) return;

    if (state.responseAssistantLoading) {
        state.pendingAssistantTriggerId = newestMsg.id;
        return;
    }

    console.log("Auto-generating narration and next-message draft:", newestMsg.id);
    handleGenerateDigest({ autoPlay: true, triggerMessageId: newestMsg.id });
}

async function loadHistory() {
    if (!activeRoomId || activeRoomId === "loading" || activeRoomId === "error") return;
    
    const currentSeq = ++liveRequestSeq;
    const targetRoomId = activeRoomId;
    
    try {
        const response = await fetch(`/api/history?roomId=${encodeURIComponent(targetRoomId)}&count=30`);
        if (!response.ok) throw new Error("HTTP error " + response.status);
        const data = await response.json();
        
        if (currentSeq !== liveRequestSeq || targetRoomId !== activeRoomId) {
            return;
        }
        
        if (data.success && data.messages) {
            const state = getRoomState(targetRoomId);
            const wasInitialized = Boolean(state.pollInitialized);
            const newRealResponses = wasInitialized
                ? data.messages.filter(message => isRealAgentResponse(message) && !state.messageMap[message.id])
                : [];
            const retryResponse = (
                wasInitialized
                && state.failedAssistantId
                && Date.now() >= (state.assistantRetryAt || 0)
            )
                ? data.messages.find(message => (
                    isRealAgentResponse(message)
                    && message.id === state.failedAssistantId
                ))
                : null;
            if (
                retryResponse
                && !newRealResponses.some(message => message.id === retryResponse.id)
            ) {
                newRealResponses.push(retryResponse);
            }
            const pendingResponse = state.pendingAssistantTriggerId
                ? data.messages.find(message => (
                    isRealAgentResponse(message)
                    && message.id === state.pendingAssistantTriggerId
                ))
                : null;
            if (
                pendingResponse
                && !newRealResponses.some(message => message.id === pendingResponse.id)
            ) {
                newRealResponses.push(pendingResponse);
            }
            if (state.loadingOlder) {
                // Preserve the pre-prepend viewport; newest page wins while loading.
                state.pendingLatest = data;
                renderRecentStats(data.stats);
                checkForAutoNarrate(targetRoomId, newRealResponses);
                return;
            }
            historyState.applyLatestPage(state, data.messages, data.has_more);
            state.pollInitialized = true;

            renderTranscriptFromState(targetRoomId);

            renderRecentStats(data.stats);
            checkForAutoNarrate(targetRoomId, newRealResponses);
        } else {
            handleTranscriptError(targetRoomId, data.detail || "Failed to load channel history.");
        }
    } catch (err) {
        if (currentSeq === liveRequestSeq && targetRoomId === activeRoomId) {
            console.error("Failed to load transcript history:", err);
            handleTranscriptError(targetRoomId, "Failed to load transcript: " + err.message);
        }
    }
}

function renderTranscriptFromState(roomId) {
    if (roomId !== activeRoomId) return;
    const state = getRoomState(roomId);
    const messages = state.orderedIds.map(id => state.messageMap[id]);
    renderTranscript(messages, state);
}

function renderTranscript(messages, state = null) {
    if (messages.length === 0) {
        transcriptFeed.innerHTML = `
            <div class="empty-state">
                <span class="material-symbols-rounded">forum</span>
                <p>No messages in this channel yet.</p>
            </div>
        `;
        return;
    }
    
    // Check if user is scrolled near bottom before update (within 120px)
    const isNearBottom = (transcriptFeed.scrollHeight - transcriptFeed.scrollTop - transcriptFeed.clientHeight) < 120;
    const isInitialLoad = (lastMessageTimestamp === null);
    
    // Check if we have new messages since last render
    let shouldScroll = false;
    if (messages.length > 0) {
        const latestMsg = messages[messages.length - 1];
        if (lastMessageTimestamp !== latestMsg.timestamp) {
            lastMessageTimestamp = latestMsg.timestamp;
            shouldScroll = true;
        }
    }
    
    // Generate HTML for Load Older button/loader at the top
    let loadOlderBtnHtml = '';
    if (state && state.hasMoreOlder) {
        if (state.loadingOlder) {
            loadOlderBtnHtml = `
                <div class="load-older-container" style="text-align: center; padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); margin-bottom: 16px;">
                    <span class="material-symbols-rounded spinning" style="font-size: 1.1rem; vertical-align: middle; display: inline-block;">progress_activity</span>
                    <span style="font-size: 0.85rem; opacity: 0.8; vertical-align: middle; margin-left: 4px;">Loading older messages...</span>
                </div>
            `;
        } else {
            loadOlderBtnHtml = `
                <div class="load-older-container" style="text-align: center; padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); margin-bottom: 16px;">
                    <button id="btn-load-older" class="btn btn-secondary btn-sm" onclick="handleLoadOlder()" style="padding: 6px 14px; font-size: 0.8rem; height: auto;">
                        <span class="material-symbols-rounded" style="font-size: 1.1rem; vertical-align: middle; margin-right: 4px;">history</span>Load Older Messages
                    </button>
                </div>
            `;
        }
    }

    // Generate HTML for messages
    const msgsHtml = messages.map(msg => {
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
    
    transcriptFeed.innerHTML = loadOlderBtnHtml + msgsHtml;

    // Auto-scroll ONLY if it's the initial room load OR user was already near the bottom
    if (state && state.savedScrollTop !== undefined && state.savedScrollTop !== null) {
        transcriptFeed.scrollTop = state.savedScrollTop;
        state.savedScrollTop = null;
    } else if (shouldScroll && (isNearBottom || isInitialLoad)) {
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
        statsBar.innerHTML = `<span class="stats-label" style="font-weight: 600; opacity: 0.7; font-size: 0.8rem; margin-right: 12px; display: inline-flex; align-items: center; gap: 4px;"><span class="material-symbols-rounded" style="font-size: 0.95rem;">history</span> Recent Window Stats:</span>` + items;
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

async function handleGenerateDigest(options = {}) {
    if (!activeRoomId) return;
    const targetRoomId = activeRoomId;
    const targetState = getRoomState(targetRoomId);
    if (targetState.responseAssistantLoading) {
        if (options.triggerMessageId) {
            targetState.pendingAssistantTriggerId = options.triggerMessageId;
        }
        return;
    }
    targetState.digestLoading = true;
    targetState.responseAssistantLoading = true;
    btnGenerateDigest.disabled = true;
    setAssistantStatus(
        options.triggerMessageId ? "Automatic: reviewing reply" : "Assistant: reviewing channel",
        "working"
    );
    digestContent.innerHTML = "<em>Reviewing the response and preparing narration plus the next draft...</em>";
    
    const settings = getSettings();
    const limit = settings.historyLimit || 20;
    // Rocket.Chat's raw window can be dominated by ACLI heartbeats and routing
    // receipts. Fetch a wider bounded page, then let the backend enforce N over
    // only real user/agent messages.
    const historyFetchCount = Math.min(100, Math.max(30, limit * 5));

    try {
        // Fetch current messages for target room with configurable context depth (U-02)
        const histResponse = await fetch(
            `/api/history?roomId=${encodeURIComponent(targetRoomId)}&count=${historyFetchCount}`
        );
        if (!histResponse.ok) throw new Error("Failed to fetch messages");
        const histData = await histResponse.json();
        
        if (!histData.success || !histData.messages || histData.messages.length === 0) {
            if (targetRoomId === activeRoomId) {
                digestContent.innerText = "No messages available to summarize.";
            }
            return;
        }
        
        // Generate the narrator text and next-message draft in one grounded AI call.
        const digestResponse = await fetch("/api/response-assistant", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
                messages: histData.messages,
                roomId: targetRoomId,
                room_name: getRoomName(targetRoomId),
                trigger_message_id: options.triggerMessageId || null,
                history_limit: limit
            })
        });
        
        const digestData = await digestResponse.json();
        if (!digestResponse.ok) throw new Error(digestData.detail || "Failed to generate response assistance");
        
        targetState.digestText = digestData.digest;
        targetState.lastAssistedId = digestData.trigger_message_id || options.triggerMessageId || null;
        targetState.failedAssistantId = null;
        targetState.assistantRetryAt = 0;
        if (targetRoomId === activeRoomId) {
            setAssistantStatus("Automatic: draft ready", "ready");
        }

        const inserted = applySuggestedDraft(targetRoomId, digestData.suggested_message);
        targetState.suggestionPhase = digestData.phase || "";
        targetState.suggestionRationale = digestData.rationale || "";
        if (inserted && targetRoomId === activeRoomId) {
            showSendFeedback(
                `AI next-step draft ready for review (${digestData.phase || "next step"}). It has not been sent.`,
                "success"
            );
            commandInput.focus();
        } else if (digestData.suggested_message && targetRoomId === activeRoomId) {
            showSendFeedback(
                "AI prepared a next-step suggestion, but your existing draft was preserved.",
                "success"
            );
        }
        
        // Render Auditable Sources List
        const sourceIds = digestData.included_message_ids || [];
        let sourceHtml = "";
        if (sourceIds.length > 0) {
            const sourceMsgs = histData.messages.filter(m => sourceIds.includes(m.id));
            sourceHtml = sourceMsgs.map(m => {
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
            targetState.sourcesToggleText = `Show Sources (${sourceMsgs.length})`;
            targetState.digestSourcesHtml = sourceHtml;
            targetState.digestSourcesVisible = true;
        } else {
            targetState.sourcesToggleText = "Show Sources (0)";
            targetState.digestSourcesHtml = "";
            targetState.digestSourcesVisible = false;
        }

        if (targetRoomId === activeRoomId) {
            currentDigestText = targetState.digestText;
            digestContent.innerText = currentDigestText;
            sourcesToggleText.innerText = targetState.sourcesToggleText;
            sourcesList.innerHTML = targetState.digestSourcesHtml;
            bindSourceItemHandlers();
            if (sourceIds.length > 0) {
                digestSourcesContainer.style.display = "block";
            } else {
                digestSourcesContainer.style.display = "none";
            }
        }

        if (targetRoomId === activeRoomId && options.autoPlay !== false) {
            handleStop();
            speakText(currentDigestText);
        }
        
    } catch (err) {
        if (targetRoomId === activeRoomId) {
            console.error("Response assistance failed:", err);
            digestContent.innerText = "Could not generate narration and next-message draft. The console will retry on a later poll.";
            setAssistantStatus("Automatic: retry scheduled", "error");
        }
        if (options.triggerMessageId) {
            targetState.failedAssistantId = options.triggerMessageId;
            targetState.assistantRetryAt = Date.now() + 30000;
        }
    } finally {
        targetState.digestLoading = false;
        targetState.responseAssistantLoading = false;
        if (targetRoomId === activeRoomId) {
            btnGenerateDigest.disabled = false;
        }
        const pendingTrigger = targetState.pendingAssistantTriggerId;
        if (
            pendingTrigger
            && pendingTrigger !== targetState.lastAssistedId
            && targetRoomId === activeRoomId
        ) {
            targetState.pendingAssistantTriggerId = null;
            handleGenerateDigest({ autoPlay: true, triggerMessageId: pendingTrigger });
        } else if (pendingTrigger === targetState.lastAssistedId) {
            targetState.pendingAssistantTriggerId = null;
        }
    }
}

function speakText(text) {
    if (
        !text
        || !('speechSynthesis' in window)
        || typeof SpeechSynthesisUtterance === "undefined"
    ) return;
    
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
function showSendFeedback(message, type) {
    const feedbackEl = document.getElementById("send-feedback");
    if (!feedbackEl) return;
    feedbackEl.textContent = message;
    feedbackEl.className = "send-feedback " + type;
    feedbackEl.style.display = "block";
    
    if (type === "success") {
        setTimeout(() => {
            if (feedbackEl.textContent === message) {
                feedbackEl.style.display = "none";
            }
        }, 5000);
    }
}

function showConfirmation() {
    let text = commandInput.value.trim();
    if (!text || !activeRoomId) return;
    
    // Auto-prepend default target agent if no @agent tag is present (U-05)
    if (!text.startsWith("@")) {
        const settings = getSettings();
        const defAgent = settings.defaultAgent || "codex";
        text = `@${defAgent} ${text}`;
        commandInput.value = text;
    }
    
    confirmationTargetRoomId = activeRoomId;
    confirmationTargetText = text;
    confirmationNonce = null;
    gatewayConfirmationSnapshot = null;
    
    // Lock text input during confirmation
    commandInput.readOnly = true;
    
    confirmationGate.classList.remove("hidden");
    btnPreSend.classList.add("hidden");
    btnConfirmSend.disabled = true;
    
    // Clear any stale feedback
    const feedbackEl = document.getElementById("send-feedback");
    if (feedbackEl) feedbackEl.style.display = "none";

    return prepareGatewayInteraction(text, activeRoomId);
}

function extractRequestedAgent(text) {
    const match = text.match(/^@([a-zA-Z0-9_]+)/);
    if (match) return match[1];
    const settings = getSettings();
    return settings.defaultAgent || "codex";
}

async function prepareGatewayInteraction(text, roomId) {
    showSendFeedback("Preparing gateway confirmation...", "success");
    try {
        const response = await fetch("/api/gateway/interact", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                schema_version: "1.0",
                client_id: "browser_console",
                input_mode: "text",
                raw_input: text,
                requested_room_id: roomId,
                requested_agent: extractRequestedAgent(text)
            })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Gateway could not prepare confirmation");

        const snapshot = data.confirmation_snapshot || data.provider_metadata?.confirmation_snapshot;
        if (!snapshot) throw new Error("Gateway response did not include a confirmation snapshot");
        if (activeRoomId !== roomId || commandInput.value.trim() !== text) return;

        gatewayConfirmationSnapshot = snapshot;
        confirmationTargetRoomId = snapshot.room_id;
        confirmationTargetText = snapshot.exact_message;
        confirmationNonce = snapshot.nonce;
        btnConfirmSend.disabled = false;
        showSendFeedback("Review and confirm the gateway-prepared transmission.", "success");
    } catch (err) {
        console.error("Gateway interaction preparation failed:", err);
        hideConfirmation();
        showSendFeedback("Could not prepare message: " + err.message, "error");
    }
}

function hideConfirmation() {
    confirmationTargetRoomId = null;
    confirmationTargetText = null;
    confirmationNonce = null;
    gatewayConfirmationSnapshot = null;
    
    // Unlock input
    commandInput.readOnly = false;
    
    confirmationGate.classList.add("hidden");
    btnPreSend.classList.remove("hidden");
    
    // Reset buttons state
    btnConfirmSend.disabled = false;
    btnCancelSend.disabled = false;
    
    const feedbackEl = document.getElementById("send-feedback");
    if (feedbackEl) {
        feedbackEl.style.display = "none";
    }
}

async function sendDraftedMessage() {
    const text = commandInput.value.trim();
    if (!text || !activeRoomId) return;
    
    // Safety check: ensure active room hasn't changed since confirmation was opened
    if (confirmationTargetRoomId && confirmationTargetRoomId !== activeRoomId) {
        showSendFeedback("Active channel changed while drafting message. Please review before sending.", "error");
        hideConfirmation();
        return;
    }
    
    // Safety check: ensure text hasn't changed
    if (confirmationTargetText && text !== confirmationTargetText) {
        showSendFeedback("Draft content changed. Please draft and confirm again.", "error");
        hideConfirmation();
        return;
    }
    
    const targetRoomId = confirmationTargetRoomId || activeRoomId;
    if (!gatewayConfirmationSnapshot) {
        showSendFeedback("Gateway confirmation is still being prepared. Please wait.", "error");
        return;
    }
    
    // Prevent double clicks/sends and cancel requests while in-flight
    btnConfirmSend.disabled = true;
    btnCancelSend.disabled = true;
    
    showSendFeedback("Transmitting confirmed gateway message...", "success");
    
    try {
        const response = await fetch("/api/gateway/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(gatewayConfirmationSnapshot)
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || "Failed to post message");
        }
        
        if (data.status === "posted") {
            commandInput.value = "";
            commandInput.readOnly = false;
            localStorage.removeItem("vc_draft_" + targetRoomId);
            const state = getRoomState(targetRoomId);
            state.draftText = "";
            hideConfirmation();
            
            // Display successful send feedback with message ID
            const msgId = data.rocket_chat_msg_ids?.[0] || "unknown";
            showSendFeedback(`Message transmitted successfully. ID: ${msgId}`, "success");
            
            loadHistory(); // Reload history immediately to see the new message
        } else {
            showSendFeedback("Error sending message to Rocket.Chat", "error");
            btnConfirmSend.disabled = false;
            btnCancelSend.disabled = false;
        }
    } catch (err) {
        console.error("Post message failed:", err);
        showSendFeedback("Failed to send message: " + err.message, "error");
        
        // Retain draft and allow retry
        btnConfirmSend.disabled = false;
        btnCancelSend.disabled = false;
    }
}
