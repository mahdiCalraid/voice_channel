// Voice Channel Console Frontend JavaScript

// State management
let currentDigestText = "";
let isPlaying = false;
let currentUtterance = null;
let currentRemoteAudio = null;
let remotePlayback = null;
let ttsProvider = "browser";
let chatterboxAvailable = false;
let voiceMode = "fast";
let speechRate = 1.0;
let lastMessageTimestamp = null;
let recognition = null;
let isListening = false;
let activeRoomId = null;
let roomsList = [];
let liveRequestSeq = 0;
let loadOlderRequestSeq = 0;
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
const niceVoiceToggle = document.getElementById("toggle-nice-voice");
const voiceModeStatus = document.getElementById("voice-mode-status");
const transcriptFeed = document.getElementById("transcript-feed");
const btnJumpToLatest = document.getElementById("btn-jump-to-latest");
const commandInput = document.getElementById("command-input");
const btnMic = document.getElementById("btn-mic");
const btnSend = document.getElementById("btn-send");
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
const channelsResizeHandle = document.getElementById("channels-resize-handle");
const btnCollapseChannels = document.getElementById("btn-collapse-channels");
const transcriptFeedContainer = document.querySelector(".transcript-feed-container");
const composerContainer = document.getElementById("composer-container");
const composerResizeHandle = document.getElementById("composer-resize-handle");
const narratorResizeHandle = document.getElementById("narrator-resize-handle");

// Keep automatic digest generation and smart drafts enabled, but require Ed to
// press Play before speech starts. A future setting can flip this feature flag.
const DISABLE_AUTO_NARRATION = true;

const COMPOSER_HEIGHT_STORAGE_KEY = "vc_composer_height_px";
// This floor leaves room for the target-agent strip, a usable writing field,
// and the Send control. The conversation pane keeps
// its independent 180px floor in getComposerResizeBounds().
const MIN_COMPOSER_HEIGHT_PX = 250;
const MIN_CONVERSATION_HEIGHT_PX = 180;
const COMPOSER_KEYBOARD_STEP_PX = 24;
let composerResizeState = null;

// The narrator starts at its historical 340px width. It can grow only while
// leaving the fixed channel rail and a useful part of the conversation visible.
const NARRATOR_WIDTH_STORAGE_KEY = "vc_narrator_width_px";
const MIN_NARRATOR_WIDTH_PX = 340;
const MIN_CONVERSATION_WIDTH_PX = 380;
const NARRATOR_KEYBOARD_STEP_PX = 24;
let narratorResizeState = null;

const MODEL_CONTROL_AGENTS = ["codex", "agy", "claude", "grok"];
const AGENT_DRAG_MIME = "application/x-voice-gateway-agent";
const QUICK_SUGGESTION_USAGE_KEY = "vc_quick_suggestion_usage";
const QUICK_SUGGESTIONS = [
    { id: "status", label: "Status?", command: "What is the status of the current task?" },
    { id: "blockers", label: "Blockers?", command: "Are there any blockers?" },
    { id: "summarize", label: "Summarize", command: "Summarize the work done so far." },
    { id: "what-changed", label: "What changed?", command: "What changed since the last update?" },
    { id: "decisions", label: "List decisions", command: "List the important decisions made so far." },
    { id: "attention", label: "What needs my attention?", command: "What needs my attention next?" }
];
const SMART_QUICK_DEFAULTS = [
    { id: "smart-0", label: "Next action", command: "What is the single next action we should take now?" },
    { id: "smart-1", label: "Your take", command: "What is your opinion on the latest update?" }
];
const QUICK_SUGGESTION_DEFAULT_ID = "status";

const CHANNELS_WIDTH_STORAGE_KEY = "vc_channels_width_px";
const CHANNELS_OPEN_STORAGE_KEY = "vc_channels_open";
const MIN_CHANNELS_WIDTH_PX = 260;
const MAX_CHANNELS_WIDTH_PX = 420;
const CHANNELS_KEYBOARD_STEP_PX = 20;
let channelsResizeState = null;

function normalizeModelControlAgent(agent) {
    const normalized = String(agent || "").trim().toLowerCase();
    return MODEL_CONTROL_AGENTS.includes(normalized) ? normalized : null;
}

function composerTextWithAgentMention(agent, currentText) {
    const normalized = normalizeModelControlAgent(agent);
    if (!normalized) return String(currentText || "");

    const body = String(currentText || "")
        .replace(/^\s+/, "")
        .replace(/^@[a-zA-Z0-9_]+\s*/, "");
    return body.trim() ? `@${normalized} ${body}` : `@${normalized} `;
}

function syncCommandDraftState(value) {
    if (activeRoomId) {
        const state = getRoomState(activeRoomId);
        state.draftText = value;
        if (state.lastInsertedSuggestion && value !== state.lastInsertedSuggestion) {
            state.lastInsertedSuggestion = null;
        }
        if (String(value || "").trim()) {
            localStorage.setItem("vc_draft_" + activeRoomId, value);
        } else {
            localStorage.removeItem("vc_draft_" + activeRoomId);
        }
    }
    applyPendingAttentionQueueIfReady();
}

function insertAgentMentionIntoComposer(agent) {
    const normalized = normalizeModelControlAgent(agent);
    if (!normalized || !commandInput || commandInput.readOnly) return false;

    const nextValue = composerTextWithAgentMention(normalized, commandInput.value);
    commandInput.value = nextValue;
    syncCommandDraftState(nextValue);
    clearComposerFeedback();
    commandInput.focus();
    if (typeof commandInput.setSelectionRange === "function") {
        commandInput.setSelectionRange(nextValue.length, nextValue.length);
    }
    return true;
}

function agentFromDragEvent(event) {
    const transfer = event && event.dataTransfer;
    if (!transfer || typeof transfer.getData !== "function") return null;
    const customAgent = normalizeModelControlAgent(transfer.getData(AGENT_DRAG_MIME));
    if (customAgent) return customAgent;
    const textAgent = String(transfer.getData("text/plain") || "").match(/^@?([a-zA-Z0-9_]+)/);
    return textAgent ? normalizeModelControlAgent(textAgent[1]) : null;
}

function bindAgentTargetControls() {
    document.querySelectorAll(".model-agent-control").forEach(btn => {
        if (btn.dataset.agentTargetInitialized === "true") return;
        btn.dataset.agentTargetInitialized = "true";
        btn.setAttribute("draggable", "true");
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            openAgentModelModal(btn.dataset.agent);
        });
        btn.addEventListener("dragstart", (e) => {
            const agent = normalizeModelControlAgent(btn.dataset.agent);
            if (!agent || !e.dataTransfer) return;
            e.dataTransfer.effectAllowed = "copy";
            e.dataTransfer.setData(AGENT_DRAG_MIME, agent);
            e.dataTransfer.setData("text/plain", `@${agent}`);
            btn.classList.add("is-dragging-agent");
        });
        btn.addEventListener("dragend", () => {
            btn.classList.remove("is-dragging-agent");
        });
    });
}

function bindComposerAgentDropTarget() {
    if (!commandInput || commandInput.dataset.agentDropInitialized === "true") return;
    commandInput.dataset.agentDropInitialized = "true";
    commandInput.addEventListener("dragover", (e) => {
        if (!agentFromDragEvent(e) || commandInput.readOnly) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
        commandInput.classList.add("agent-drop-target");
    });
    commandInput.addEventListener("dragenter", (e) => {
        if (agentFromDragEvent(e) && !commandInput.readOnly) {
            commandInput.classList.add("agent-drop-target");
        }
    });
    commandInput.addEventListener("dragleave", () => {
        commandInput.classList.remove("agent-drop-target");
    });
    commandInput.addEventListener("drop", (e) => {
        const agent = agentFromDragEvent(e);
        if (!agent) return;
        e.preventDefault();
        commandInput.classList.remove("agent-drop-target");
        insertAgentMentionIntoComposer(agent);
    });
}

function initAgentTargeting() {
    bindAgentTargetControls();
    bindComposerAgentDropTarget();
}

function quickSuggestionById(id) {
    return QUICK_SUGGESTIONS.find(suggestion => suggestion.id === id) || null;
}

function readQuickSuggestionUsage() {
    try {
        const stored = JSON.parse(localStorage.getItem(QUICK_SUGGESTION_USAGE_KEY) || "{}");
        return stored && typeof stored === "object" ? stored : {};
    } catch (e) {
        return {};
    }
}

function mostUsedQuickSuggestion() {
    const usage = readQuickSuggestionUsage();
    let best = quickSuggestionById(QUICK_SUGGESTION_DEFAULT_ID);
    let bestCount = Number(usage[best.id] || 0);

    QUICK_SUGGESTIONS.forEach(suggestion => {
        const count = Number(usage[suggestion.id] || 0);
        if (count > bestCount) {
            best = suggestion;
            bestCount = count;
        }
    });
    return best;
}

function updateMostUsedQuickSuggestionButton() {
    const button = document.getElementById("quick-suggestion-most-used");
    const suggestion = mostUsedQuickSuggestion();
    if (!button || !suggestion) return suggestion;

    button.textContent = suggestion.label;
    button.dataset.quickSuggestion = "most-used";
    button.title = `Most-used predefined quick suggestion: ${suggestion.label}`;
    if (typeof button.setAttribute === "function") {
        button.setAttribute("aria-label", `Most-used quick suggestion: ${suggestion.label}`);
    }
    return suggestion;
}

function recordQuickSuggestionUse(id) {
    const usage = readQuickSuggestionUsage();
    usage[id] = Number(usage[id] || 0) + 1;
    try {
        localStorage.setItem(QUICK_SUGGESTION_USAGE_KEY, JSON.stringify(usage));
    } catch (e) {
        // Usage is a convenience only; failure must not block inserting a message.
    }
}

function getSmartQuickSuggestions(roomId = activeRoomId) {
    if (!roomId) return SMART_QUICK_DEFAULTS.map(item => ({ ...item }));
    const state = getRoomState(roomId);
    if (Array.isArray(state.smartQuickSuggestions) && state.smartQuickSuggestions.length === 2) {
        return state.smartQuickSuggestions;
    }
    return SMART_QUICK_DEFAULTS.map(item => ({ ...item }));
}

function normalizeSmartQuickSuggestions(items) {
    if (!Array.isArray(items)) return null;
    const cleaned = [];
    for (const item of items) {
        if (!item || typeof item !== "object") continue;
        const label = String(item.label || "").trim().split(/\s+/).slice(0, 2).join(" ");
        const command = String(item.command || "").trim();
        if (!label || !command) continue;
        cleaned.push({
            id: `smart-${cleaned.length}`,
            label: label.slice(0, 22),
            command: command.slice(0, 500),
        });
        if (cleaned.length >= 2) break;
    }
    return cleaned.length === 2 ? cleaned : null;
}

function applySmartQuickSuggestions(roomId, items) {
    const normalized = normalizeSmartQuickSuggestions(items) || SMART_QUICK_DEFAULTS.map(item => ({ ...item }));
    if (roomId) {
        getRoomState(roomId).smartQuickSuggestions = normalized;
    }
    if (!roomId || roomId === activeRoomId) {
        renderSmartQuickSuggestionButtons(normalized);
    }
    return normalized;
}

function renderSmartQuickSuggestionButtons(items) {
    const suggestions = Array.isArray(items) && items.length === 2
        ? items
        : getSmartQuickSuggestions(activeRoomId);
    suggestions.forEach((suggestion, index) => {
        const button = document.getElementById(`quick-suggestion-smart-${index}`);
        if (!button) return;
        button.textContent = suggestion.label;
        button.dataset.quickSuggestion = suggestion.id;
        button.disabled = false;
        if (typeof button.setAttribute === "function") {
            button.setAttribute("aria-disabled", "false");
            button.setAttribute("aria-label", `Smart quick suggestion: ${suggestion.label}`);
        }
        if (button.classList && typeof button.classList.remove === "function") {
            button.classList.remove("quick-suggestion-placeholder");
            button.classList.add("quick-suggestion-smart");
        }
        button.title = `AI smart quick suggestion: ${suggestion.command}`;
    });
}

function insertQuickSuggestion(id) {
    if (!commandInput || commandInput.readOnly) return false;

    let suggestion = null;
    let recordUsageId = null;

    if (id === "most-used") {
        suggestion = mostUsedQuickSuggestion();
        recordUsageId = suggestion ? suggestion.id : null;
    } else if (String(id || "").startsWith("smart-")) {
        const smart = getSmartQuickSuggestions(activeRoomId);
        suggestion = smart.find(item => item.id === id) || null;
    } else {
        suggestion = quickSuggestionById(id);
        recordUsageId = suggestion ? suggestion.id : null;
    }

    if (!suggestion) return false;

    commandInput.value = suggestion.command;
    // Quick suggestions are user-selected composer text. They intentionally
    // do not set lastInsertedSuggestion, which is reserved for narrator drafts.
    syncCommandDraftState(commandInput.value);
    clearComposerFeedback();
    if (recordUsageId) {
        recordQuickSuggestionUse(recordUsageId);
        updateMostUsedQuickSuggestionButton();
    }
    commandInput.focus();
    return true;
}

function setQuickSuggestionsMenuOpen(isOpen) {
    const menu = document.getElementById("quick-suggestions-menu");
    const moreButton = document.getElementById("btn-more-quick-suggestions");
    if (!menu || !moreButton) return;
    menu.classList.toggle("hidden", !isOpen);
    moreButton.setAttribute("aria-expanded", String(Boolean(isOpen)));
}

function bindQuickSuggestionControls() {
    const moreButton = document.getElementById("btn-more-quick-suggestions");
    if (moreButton && moreButton.dataset.quickSuggestionsInitialized !== "true") {
        moreButton.dataset.quickSuggestionsInitialized = "true";
        moreButton.addEventListener("click", event => {
            event.stopPropagation();
            const menu = document.getElementById("quick-suggestions-menu");
            setQuickSuggestionsMenuOpen(Boolean(menu && menu.classList.contains("hidden")));
        });
    }

    document.querySelectorAll(".quick-suggestion-btn, .quick-suggestion-menu-item").forEach(button => {
        if (button.dataset.quickSuggestionInitialized === "true") return;
        button.dataset.quickSuggestionInitialized = "true";
        button.addEventListener("click", () => {
            if (insertQuickSuggestion(button.dataset.quickSuggestion)) {
                setQuickSuggestionsMenuOpen(false);
            }
        });
    });

    if (document.body && document.body.dataset.quickSuggestionsOutsideInitialized !== "true") {
        document.body.dataset.quickSuggestionsOutsideInitialized = "true";
        document.addEventListener("click", event => {
            const container = document.getElementById("quick-suggestions");
            if (container && !container.contains(event.target)) {
                setQuickSuggestionsMenuOpen(false);
            }
        });
        document.addEventListener("keydown", event => {
            if (event.key === "Escape") {
                setQuickSuggestionsMenuOpen(false);
            }
        });
    }

    updateMostUsedQuickSuggestionButton();
    renderSmartQuickSuggestionButtons();
}

function bindMessageComposer() {
    if (btnSend && btnSend.dataset.messageSendInitialized !== "true") {
        btnSend.dataset.messageSendInitialized = "true";
        btnSend.addEventListener("click", sendMessage);
    }
    if (commandInput && commandInput.dataset.messageSendInitialized !== "true") {
        commandInput.dataset.messageSendInitialized = "true";
        commandInput.addEventListener("keydown", event => {
            if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
            event.preventDefault();
            return sendMessage();
        });
    }
}

// Settings Management (U-01, U-02, U-03, U-05)
const SETTINGS_VERSION = 5;
const THEMES = {
    midnight: "Midnight",
    "quiet-light": "Quiet Light",
    nord: "Nord",
    "high-contrast": "High Contrast",
    "paper-light": "Paper Light",
    "solarized-light": "Solarized Light",
    "rose-pine-dawn": "Rosé Pine Dawn",
    "mist-light": "Mist Light",
    "sepia-light": "Sepia Light",
    dracula: "Dracula",
    "one-dark": "One Dark",
    "solarized-dark": "Solarized Dark",
    "tokyo-night": "Tokyo Night",
    "catppuccin-mocha": "Catppuccin Mocha"
};
const THEME_COLOR_SCHEMES = {
    "quiet-light": "light",
    "paper-light": "light",
    "solarized-light": "light",
    "rose-pine-dawn": "light",
    "mist-light": "light",
    "sepia-light": "light",
    midnight: "dark",
    nord: "dark",
    "high-contrast": "dark",
    dracula: "dark",
    "one-dark": "dark",
    "solarized-dark": "dark",
    "tokyo-night": "dark",
    "catppuccin-mocha": "dark"
};
const SYSTEM_FONT_SIZES = {
    normal: 16,
    medium: 20,
    large: 26,
    xlarge: 35
};
const CHAT_FONT_FAMILIES = {
    outfit: "'Outfit', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    system: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    arial: "Arial, Helvetica, sans-serif",
    verdana: "Verdana, Geneva, sans-serif",
    trebuchet: "'Trebuchet MS', Arial, sans-serif",
    georgia: "Georgia, 'Times New Roman', serif",
    times: "'Times New Roman', Times, serif",
    jetbrains: "'JetBrains Mono', Menlo, Consolas, monospace"
};
const DEFAULT_SETTINGS = {
    settingsVersion: SETTINGS_VERSION,
    theme: "midnight",
    systemFontSize: "medium",
    chatFontFamily: "outfit",
    chatFontSize: 18,
    chatLineHeight: 1.6,
    chatParagraphSpacing: 8,
    chatFontWeight: 400,
    chatLetterSpacing: 0,
    historyLimit: 20,
    defaultAgent: "codex",
    autoNarrate: true,
    backgroundNarrationPrewarm: true,
    backgroundNarrationPrewarmScope: "top_attention",
    autoScrollNewMessages: false,
    voiceMode: "fast"
};

const AUTOMATIC_ASSISTANCE_LEASE_MS = 90 * 1000;
const AUTOMATIC_ASSISTANCE_DONE_MS = 60 * 60 * 1000;
const responseAssistantTabId = `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function automaticAssistantStorageKey(kind, roomId, triggerMessageId) {
    return `vc_response_assistant_${kind}_${encodeURIComponent(roomId)}_${encodeURIComponent(triggerMessageId)}`;
}

function isAutomaticAssistanceCompleted(roomId, triggerMessageId) {
    try {
        const key = automaticAssistantStorageKey("done", roomId, triggerMessageId);
        const completedAt = Number(localStorage.getItem(key) || 0);
        if (!completedAt) return false;
        if (Date.now() - completedAt <= AUTOMATIC_ASSISTANCE_DONE_MS) return true;
        localStorage.removeItem(key);
    } catch (e) {
        console.warn("Could not read the cross-tab response-assistant marker:", e);
    }
    return false;
}

function tryClaimAutomaticAssistance(roomId, triggerMessageId) {
    if (isAutomaticAssistanceCompleted(roomId, triggerMessageId)) {
        return { claimed: false, coordinated: true };
    }
    try {
        const key = automaticAssistantStorageKey("lease", roomId, triggerMessageId);
        const now = Date.now();
        const existing = JSON.parse(localStorage.getItem(key) || "null");
        if (
            existing
            && existing.owner !== responseAssistantTabId
            && Number(existing.expiresAt || 0) > now
        ) {
            return { claimed: false, coordinated: true };
        }
        const claim = {
            owner: responseAssistantTabId,
            expiresAt: now + AUTOMATIC_ASSISTANCE_LEASE_MS
        };
        localStorage.setItem(key, JSON.stringify(claim));
        const stored = JSON.parse(localStorage.getItem(key) || "null");
        return {
            claimed: Boolean(stored && stored.owner === responseAssistantTabId),
            coordinated: true
        };
    } catch (e) {
        // The backend idempotency key remains authoritative when storage is
        // unavailable (private mode, quota errors, or a restricted webview).
        console.warn("Cross-tab response-assistant lease is unavailable:", e);
        return { claimed: true, coordinated: false };
    }
}

function touchAutomaticAssistanceClaim(roomId, triggerMessageId) {
    try {
        const key = automaticAssistantStorageKey("lease", roomId, triggerMessageId);
        const existing = JSON.parse(localStorage.getItem(key) || "null");
        if (existing && existing.owner === responseAssistantTabId) {
            existing.expiresAt = Date.now() + AUTOMATIC_ASSISTANCE_LEASE_MS;
            localStorage.setItem(key, JSON.stringify(existing));
            return true;
        }
    } catch (e) {
        // Storage disabled or restricted
    }
    return false;
}

function ownsAutomaticAssistanceClaim(roomId, triggerMessageId) {
    try {
        const key = automaticAssistantStorageKey("lease", roomId, triggerMessageId);
        const stored = JSON.parse(localStorage.getItem(key) || "null");
        return Boolean(
            stored
            && stored.owner === responseAssistantTabId
            && Number(stored.expiresAt || 0) > Date.now()
        );
    } catch (e) {
        console.warn("Could not verify the cross-tab response-assistant lease:", e);
        return false;
    }
}

function markAutomaticAssistanceCompleted(roomId, triggerMessageId) {
    try {
        const key = automaticAssistantStorageKey("done", roomId, triggerMessageId);
        localStorage.setItem(key, String(Date.now()));
    } catch (e) {
        console.warn("Could not save the cross-tab response-assistant marker:", e);
    }
}

function releaseAutomaticAssistanceClaim(roomId, triggerMessageId) {
    try {
        const key = automaticAssistantStorageKey("lease", roomId, triggerMessageId);
        const stored = JSON.parse(localStorage.getItem(key) || "null");
        if (stored && stored.owner === responseAssistantTabId) {
            localStorage.removeItem(key);
        }
    } catch (e) {
        console.warn("Could not release the cross-tab response-assistant lease:", e);
    }
}

function getSettings() {
    try {
        const raw = localStorage.getItem("vc_settings");
        if (!raw) return { ...DEFAULT_SETTINGS };
        const stored = JSON.parse(raw);
        // U-03 originally shipped disabled by default. Migrate that placeholder
        // setting once so the automatic narrator + suggestion loop is actually on.
        if (!stored.settingsVersion || stored.settingsVersion < 2) {
            stored.autoNarrate = true;
        }
        if (!stored.settingsVersion || stored.settingsVersion < SETTINGS_VERSION) {
            stored.systemFontSize = stored.systemFontSize || stored.fontSize || "medium";
        }
        stored.settingsVersion = SETTINGS_VERSION;
        return normalizeSettings({ ...DEFAULT_SETTINGS, ...stored });
    } catch (e) {
        return { ...DEFAULT_SETTINGS };
    }
}

function clampSettingNumber(value, fallback, minimum, maximum) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
}

function normalizeSettings(settings = {}) {
    const theme = Object.prototype.hasOwnProperty.call(THEMES, settings.theme)
        ? settings.theme
        : DEFAULT_SETTINGS.theme;
    const systemFontSize = Object.prototype.hasOwnProperty.call(SYSTEM_FONT_SIZES, settings.systemFontSize)
        ? settings.systemFontSize
        : DEFAULT_SETTINGS.systemFontSize;
    const chatFontFamily = Object.prototype.hasOwnProperty.call(CHAT_FONT_FAMILIES, settings.chatFontFamily)
        ? settings.chatFontFamily
        : DEFAULT_SETTINGS.chatFontFamily;
    const voiceMode = settings.voiceMode === "nice" ? "nice" : "fast";
    const backgroundNarrationPrewarmScope = settings.backgroundNarrationPrewarmScope === "supervised"
        ? "supervised"
        : "top_attention";
    return {
        ...DEFAULT_SETTINGS,
        ...settings,
        settingsVersion: SETTINGS_VERSION,
        theme,
        systemFontSize,
        chatFontFamily,
        voiceMode,
        backgroundNarrationPrewarm: settings.backgroundNarrationPrewarm !== false,
        backgroundNarrationPrewarmScope,
        chatFontSize: clampSettingNumber(settings.chatFontSize, DEFAULT_SETTINGS.chatFontSize, 12, 36),
        chatLineHeight: clampSettingNumber(settings.chatLineHeight, DEFAULT_SETTINGS.chatLineHeight, 1, 2.4),
        chatParagraphSpacing: clampSettingNumber(
            settings.chatParagraphSpacing,
            DEFAULT_SETTINGS.chatParagraphSpacing,
            0,
            28
        ),
        chatFontWeight: clampSettingNumber(settings.chatFontWeight, DEFAULT_SETTINGS.chatFontWeight, 300, 600),
        chatLetterSpacing: clampSettingNumber(
            settings.chatLetterSpacing,
            DEFAULT_SETTINGS.chatLetterSpacing,
            -0.5,
            2
        )
    };
}

function saveSettings(settings) {
    try {
        const normalized = normalizeSettings(settings);
        localStorage.setItem("vc_settings", JSON.stringify(normalized));
        applySettings(normalized);
    } catch (e) {
        console.error("Failed to save settings:", e);
    }
}

function saveNarrationPrewarmSettings(settings) {
    // This setting must reach the Gateway because background work continues
    // while this browser is looking at a different room. It stores only
    // control metadata; the generated digest remains memory-only server-side.
    const scope = settings.backgroundNarrationPrewarmScope === "supervised"
        ? "supervised"
        : "top_attention";
    fetch("/api/narration/prewarm/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            enabled: Boolean(settings.autoNarrate && settings.backgroundNarrationPrewarm),
            scope,
            top_n: 5,
            history_limit: settings.historyLimit || 20
        })
    }).catch(error => {
        console.warn("Could not save background narration prewarm setting:", error);
    });
}

function applySettings(settings = getSettings()) {
    const normalized = normalizeSettings(settings);
    voiceMode = normalized.voiceMode;
    updateVoiceModeUI(normalized.voiceMode);
    document.body.dataset.theme = normalized.theme;
    document.body.setAttribute("data-theme", normalized.theme);
    document.body.style.setProperty(
        "color-scheme",
        THEME_COLOR_SCHEMES[normalized.theme] || "dark"
    );
    document.body.classList.remove("font-normal", "font-medium", "font-large", "font-xlarge");
    document.body.classList.add("font-" + normalized.systemFontSize);
    document.body.style.setProperty("--system-font-size", SYSTEM_FONT_SIZES[normalized.systemFontSize] + "px");
    document.body.style.setProperty("--chat-font-family", CHAT_FONT_FAMILIES[normalized.chatFontFamily]);
    document.body.style.setProperty("--chat-font-size", normalized.chatFontSize + "px");
    document.body.style.setProperty("--chat-line-height", String(normalized.chatLineHeight));
    document.body.style.setProperty("--chat-paragraph-spacing", normalized.chatParagraphSpacing + "px");
    document.body.style.setProperty("--chat-font-weight", String(normalized.chatFontWeight));
    document.body.style.setProperty("--chat-letter-spacing", normalized.chatLetterSpacing + "px");
    if (normalized.autoNarrate) {
        setAssistantStatus("Automatic: waiting", "on");
    } else {
        setAssistantStatus("Automatic: off", "off");
    }
}

function updateTTSModeStatus() {
    if (!ttsStatusChip) return;
    const label = ttsStatusChip.querySelector(".status-label");
    if (!label) return;
    if (voiceMode === "nice") {
        label.innerText = chatterboxAvailable
            ? "Nice voice: Ready"
            : "Nice voice unavailable · Fast fallback";
    } else {
        label.innerText = "Fast voice: Ready";
    }
}

function updateVoiceModeUI(mode = voiceMode) {
    voiceMode = mode === "nice" ? "nice" : "fast";
    if (niceVoiceToggle) {
        niceVoiceToggle.checked = voiceMode === "nice";
        if (typeof niceVoiceToggle.setAttribute === "function") {
            niceVoiceToggle.setAttribute("aria-checked", String(voiceMode === "nice"));
        }
    }
    if (voiceModeStatus) {
        voiceModeStatus.innerText = voiceMode === "nice"
            ? (chatterboxAvailable
                ? "Server Chatterbox voice — nicer, but slower"
                : "Chatterbox is unavailable — fast voice will be used")
            : "Fast browser voice — starts immediately";
    }
    updateTTSModeStatus();
}

function setVoiceMode(mode, persist = true) {
    const nextMode = mode === "nice" ? "nice" : "fast";
    if (isPlaying || currentRemoteAudio || remotePlayback || currentUtterance) {
        handleStop();
    }
    const nextSettings = normalizeSettings({ ...getSettings(), voiceMode: nextMode });
    if (persist) {
        saveSettings(nextSettings);
    } else {
        applySettings(nextSettings);
    }
    updateVoiceModeUI(nextMode);
}

function initVoiceModeControl() {
    if (!niceVoiceToggle || niceVoiceToggle.dataset.voiceModeInitialized === "true") return;
    niceVoiceToggle.dataset.voiceModeInitialized = "true";
    niceVoiceToggle.addEventListener("change", () => {
        setVoiceMode(niceVoiceToggle.checked ? "nice" : "fast");
    });
    updateVoiceModeUI(getSettings().voiceMode);
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

function narrationTextFromEditor() {
    if (!digestContent) return "";
    return String(digestContent.innerText || "")
        .replace(/\u00a0/g, " ")
        .trim();
}

function updateNarrationPlayState() {
    if (!btnPlayPause) return;
    const hasNarration = Boolean(String(currentDigestText || "").trim());
    btnPlayPause.disabled = !hasNarration;
    btnPlayPause.title = hasNarration
        ? "Play or pause summary narration"
        : "Nothing to play yet";
}

function handleNarrationEdit() {
    currentDigestText = narrationTextFromEditor();
    if (activeRoomId) {
        getRoomState(activeRoomId).digestText = currentDigestText;
    }
    if (window.speechSynthesis.speaking || currentUtterance) {
        handleStop();
    }
    updateNarrationPlayState();
}

function initSettingsModal() {
    const btnOpen = document.getElementById("btn-open-settings");
    const btnClose = document.getElementById("btn-close-settings");
    const btnSave = document.getElementById("btn-save-settings");
    const modal = document.getElementById("settings-modal");

    const selSystemFontSize = document.getElementById("setting-system-font-size");
    const selTheme = document.getElementById("setting-theme");
    const selChatFontFamily = document.getElementById("setting-chat-font-family");
    const inputChatFontSize = document.getElementById("setting-chat-font-size");
    const inputChatLineHeight = document.getElementById("setting-chat-line-height");
    const inputChatParagraphSpacing = document.getElementById("setting-chat-paragraph-spacing");
    const selChatFontWeight = document.getElementById("setting-chat-font-weight");
    const inputChatLetterSpacing = document.getElementById("setting-chat-letter-spacing");
    const outputChatLineHeight = document.getElementById("setting-chat-line-height-value");
    const outputChatParagraphSpacing = document.getElementById("setting-chat-paragraph-spacing-value");
    const outputChatLetterSpacing = document.getElementById("setting-chat-letter-spacing-value");
    const selHistoryLimit = document.getElementById("setting-history-limit");
    const selDefaultAgent = document.getElementById("setting-default-agent");
    const chkAutoNarrate = document.getElementById("setting-auto-narrate");
    const chkBackgroundNarrationPrewarm = document.getElementById("setting-background-narration-prewarm");
    const selBackgroundNarrationPrewarmScope = document.getElementById("setting-background-narration-prewarm-scope");
    const chkAutoScrollNewMessages = document.getElementById("setting-auto-scroll-new-messages");
    const typographyControls = [
        selSystemFontSize,
        selChatFontFamily,
        inputChatFontSize,
        inputChatLineHeight,
        inputChatParagraphSpacing,
        selChatFontWeight,
        inputChatLetterSpacing
    ].filter(Boolean);
    const appearanceControls = [selTheme].filter(Boolean);
    let settingsBeforePreview = null;

    if (!btnOpen || !modal) return;

    const updateTypographyOutputs = () => {
        if (outputChatLineHeight && inputChatLineHeight) {
            outputChatLineHeight.value = Number(inputChatLineHeight.value).toFixed(2);
        }
        if (outputChatParagraphSpacing && inputChatParagraphSpacing) {
            outputChatParagraphSpacing.value = inputChatParagraphSpacing.value + "px";
        }
        if (outputChatLetterSpacing && inputChatLetterSpacing) {
            outputChatLetterSpacing.value = inputChatLetterSpacing.value + "px";
        }
    };

    const settingsFromForm = (baseSettings = getSettings()) => normalizeSettings({
        ...baseSettings,
        settingsVersion: SETTINGS_VERSION,
        theme: selTheme ? selTheme.value : DEFAULT_SETTINGS.theme,
        systemFontSize: selSystemFontSize ? selSystemFontSize.value : DEFAULT_SETTINGS.systemFontSize,
        chatFontFamily: selChatFontFamily ? selChatFontFamily.value : DEFAULT_SETTINGS.chatFontFamily,
        chatFontSize: inputChatFontSize ? inputChatFontSize.value : DEFAULT_SETTINGS.chatFontSize,
        chatLineHeight: inputChatLineHeight ? inputChatLineHeight.value : DEFAULT_SETTINGS.chatLineHeight,
        chatParagraphSpacing: inputChatParagraphSpacing
            ? inputChatParagraphSpacing.value
            : DEFAULT_SETTINGS.chatParagraphSpacing,
        chatFontWeight: selChatFontWeight ? selChatFontWeight.value : DEFAULT_SETTINGS.chatFontWeight,
        chatLetterSpacing: inputChatLetterSpacing
            ? inputChatLetterSpacing.value
            : DEFAULT_SETTINGS.chatLetterSpacing,
        historyLimit: selHistoryLimit ? parseInt(selHistoryLimit.value, 10) : 20,
        defaultAgent: selDefaultAgent ? selDefaultAgent.value : "codex",
        autoNarrate: chkAutoNarrate ? chkAutoNarrate.checked : false,
        backgroundNarrationPrewarm: chkBackgroundNarrationPrewarm
            ? chkBackgroundNarrationPrewarm.checked
            : DEFAULT_SETTINGS.backgroundNarrationPrewarm,
        backgroundNarrationPrewarmScope: selBackgroundNarrationPrewarmScope
            ? selBackgroundNarrationPrewarmScope.value
            : DEFAULT_SETTINGS.backgroundNarrationPrewarmScope,
        autoScrollNewMessages: chkAutoScrollNewMessages ? chkAutoScrollNewMessages.checked : false
    });

    const populateSettingsForm = settings => {
        if (selTheme) selTheme.value = settings.theme;
        if (selSystemFontSize) selSystemFontSize.value = settings.systemFontSize;
        if (selChatFontFamily) selChatFontFamily.value = settings.chatFontFamily;
        if (inputChatFontSize) inputChatFontSize.value = String(settings.chatFontSize);
        if (inputChatLineHeight) inputChatLineHeight.value = String(settings.chatLineHeight);
        if (inputChatParagraphSpacing) {
            inputChatParagraphSpacing.value = String(settings.chatParagraphSpacing);
        }
        if (selChatFontWeight) selChatFontWeight.value = String(settings.chatFontWeight);
        if (inputChatLetterSpacing) inputChatLetterSpacing.value = String(settings.chatLetterSpacing);
        if (selHistoryLimit) selHistoryLimit.value = String(settings.historyLimit || 20);
        if (selDefaultAgent) selDefaultAgent.value = settings.defaultAgent || "codex";
        if (chkAutoNarrate) chkAutoNarrate.checked = !!settings.autoNarrate;
        if (chkBackgroundNarrationPrewarm) {
            chkBackgroundNarrationPrewarm.checked = !!settings.backgroundNarrationPrewarm;
        }
        if (selBackgroundNarrationPrewarmScope) {
            selBackgroundNarrationPrewarmScope.value = settings.backgroundNarrationPrewarmScope === "supervised"
                ? "supervised"
                : "top_attention";
        }
        if (chkAutoScrollNewMessages) chkAutoScrollNewMessages.checked = !!settings.autoScrollNewMessages;
        updateTypographyOutputs();
    };

    btnOpen.addEventListener("click", () => {
        const settings = getSettings();
        settingsBeforePreview = settings;
        populateSettingsForm(settings);
        modal.classList.remove("hidden");
    });

    const closeModal = (revertPreview = true) => {
        if (revertPreview && settingsBeforePreview) applySettings(settingsBeforePreview);
        settingsBeforePreview = null;
        modal.classList.add("hidden");
    };
    if (btnClose) btnClose.addEventListener("click", () => closeModal(true));

    const previewTypography = () => {
        updateTypographyOutputs();
        applySettings(settingsFromForm(settingsBeforePreview || getSettings()));
    };
    typographyControls.forEach(control => {
        control.addEventListener("input", previewTypography);
        control.addEventListener("change", previewTypography);
    });
    appearanceControls.forEach(control => {
        control.addEventListener("change", previewTypography);
    });

    if (btnSave) {
        btnSave.addEventListener("click", () => {
            const updated = settingsFromForm(settingsBeforePreview || getSettings());
            saveSettings(updated);
            saveNarrationPrewarmSettings(updated);
            settingsBeforePreview = null;
            closeModal(false);
        });
    }
}

function elementHeight(element) {
    if (!element) return 0;
    if (typeof element.getBoundingClientRect === "function") {
        const rect = element.getBoundingClientRect();
        if (rect && Number.isFinite(rect.height) && rect.height > 0) return rect.height;
    }
    const inlineHeight = parseFloat(element.style && element.style.height);
    if (Number.isFinite(inlineHeight) && inlineHeight > 0) return inlineHeight;
    return Number(element.offsetHeight || element.clientHeight || 0);
}

function getComposerResizeBounds() {
    const currentComposerHeight = elementHeight(composerContainer) || MIN_COMPOSER_HEIGHT_PX;
    const currentConversationHeight = elementHeight(transcriptFeedContainer) || MIN_CONVERSATION_HEIGHT_PX;
    const splitHeight = Math.max(
        MIN_COMPOSER_HEIGHT_PX + MIN_CONVERSATION_HEIGHT_PX,
        currentComposerHeight + currentConversationHeight
    );
    return {
        minimum: MIN_COMPOSER_HEIGHT_PX,
        maximum: Math.max(MIN_COMPOSER_HEIGHT_PX, splitHeight - MIN_CONVERSATION_HEIGHT_PX)
    };
}

function setComposerHeight(requestedHeight, persist = true) {
    if (!composerContainer) return null;
    const bounds = getComposerResizeBounds();
    const numericHeight = Number(requestedHeight);
    const fallback = elementHeight(composerContainer) || bounds.minimum;
    const height = Math.round(Math.min(bounds.maximum, Math.max(
        bounds.minimum,
        Number.isFinite(numericHeight) ? numericHeight : fallback
    )));
    composerContainer.style.height = height + "px";
    if (composerResizeHandle) {
        composerResizeHandle.setAttribute("aria-valuemin", String(bounds.minimum));
        composerResizeHandle.setAttribute("aria-valuemax", String(Math.round(bounds.maximum)));
        composerResizeHandle.setAttribute("aria-valuenow", String(height));
    }
    if (persist) {
        try {
            localStorage.setItem(COMPOSER_HEIGHT_STORAGE_KEY, String(height));
        } catch (e) {
            console.warn("Could not persist composer height:", e);
        }
    }
    return height;
}

function beginComposerResize(event) {
    if (!composerContainer || !composerResizeHandle) return;
    if (typeof event.button === "number" && event.button !== 0) return;
    composerResizeState = {
        pointerId: event.pointerId,
        startY: Number(event.clientY || 0),
        startHeight: elementHeight(composerContainer) || MIN_COMPOSER_HEIGHT_PX
    };
    document.body.classList.add("is-resizing-composer");
    if (typeof composerResizeHandle.setPointerCapture === "function" && event.pointerId != null) {
        composerResizeHandle.setPointerCapture(event.pointerId);
    }
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function resizeComposerFromPointer(event) {
    if (!composerResizeState) return;
    if (
        composerResizeState.pointerId != null
        && event.pointerId != null
        && event.pointerId !== composerResizeState.pointerId
    ) return;
    const delta = composerResizeState.startY - Number(event.clientY || 0);
    setComposerHeight(composerResizeState.startHeight + delta, false);
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function finishComposerResize(event = {}) {
    if (!composerResizeState) return;
    if (
        composerResizeState.pointerId != null
        && event.pointerId != null
        && event.pointerId !== composerResizeState.pointerId
    ) return;
    composerResizeState = null;
    document.body.classList.remove("is-resizing-composer");
    setComposerHeight(elementHeight(composerContainer), true);
}

function resizeComposerFromKeyboard(event) {
    if (!composerContainer) return;
    const currentHeight = elementHeight(composerContainer) || MIN_COMPOSER_HEIGHT_PX;
    const bounds = getComposerResizeBounds();
    let nextHeight = null;
    if (event.key === "ArrowUp") nextHeight = currentHeight + COMPOSER_KEYBOARD_STEP_PX;
    if (event.key === "ArrowDown") nextHeight = currentHeight - COMPOSER_KEYBOARD_STEP_PX;
    if (event.key === "Home") nextHeight = bounds.minimum;
    if (event.key === "End") nextHeight = bounds.maximum;
    if (nextHeight === null) return;
    setComposerHeight(nextHeight, true);
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function initComposerResizer() {
    if (!composerResizeHandle || !composerContainer || !transcriptFeedContainer) return;
    if (composerResizeHandle.dataset.resizeInitialized === "true") return;
    composerResizeHandle.dataset.resizeInitialized = "true";
    composerResizeHandle.addEventListener("pointerdown", beginComposerResize);
    composerResizeHandle.addEventListener("keydown", resizeComposerFromKeyboard);
    document.addEventListener("pointermove", resizeComposerFromPointer);
    document.addEventListener("pointerup", finishComposerResize);
    document.addEventListener("pointercancel", finishComposerResize);
    if (typeof window.addEventListener === "function") {
        window.addEventListener("resize", () => {
            try {
                const savedHeight = Number(localStorage.getItem(COMPOSER_HEIGHT_STORAGE_KEY));
                if (Number.isFinite(savedHeight) && savedHeight > 0) {
                    setComposerHeight(savedHeight, false);
                }
            } catch (e) {
                setComposerHeight(elementHeight(composerContainer), false);
            }
        });
    }
    try {
        const savedHeight = Number(localStorage.getItem(COMPOSER_HEIGHT_STORAGE_KEY));
        if (Number.isFinite(savedHeight) && savedHeight > 0) {
            setComposerHeight(savedHeight, false);
        } else {
            setComposerHeight(elementHeight(composerContainer), false);
        }
    } catch (e) {
        setComposerHeight(elementHeight(composerContainer), false);
    }
}

function elementWidth(element) {
    if (!element) return 0;
    if (typeof element.getBoundingClientRect === "function") {
        const rect = element.getBoundingClientRect();
        if (rect && Number.isFinite(rect.width) && rect.width > 0) return rect.width;
    }
    const inlineWidth = parseFloat(element.style && element.style.width);
    if (Number.isFinite(inlineWidth) && inlineWidth > 0) return inlineWidth;
    return Number(element.offsetWidth || element.clientWidth || 0);
}

function viewportWidth() {
    const width = Number(window.innerWidth || (document.documentElement && document.documentElement.clientWidth));
    return Number.isFinite(width) && width > 0 ? width : 0;
}

function currentNarratorWidth() {
    if (!narratorSidebar) return MIN_NARRATOR_WIDTH_PX;
    const storedWidth = parseFloat(
        narratorSidebar.style && narratorSidebar.style.getPropertyValue
            ? narratorSidebar.style.getPropertyValue("--narrator-sidebar-width")
            : ""
    );
    return Number.isFinite(storedWidth) && storedWidth > 0
        ? storedWidth
        : (elementWidth(narratorSidebar) || MIN_NARRATOR_WIDTH_PX);
}

function getNarratorResizeBounds() {
    const width = viewportWidth();
    const channelWidth = elementWidth(channelsSidebar) || 280;
    const handleWidth = elementWidth(narratorResizeHandle) || 12;
    // At smaller viewports the narrator becomes an overlay, so its desktop
    // resizing limit is irrelevant. Keep the current size stable in that mode.
    const maximum = width > 1100
        ? Math.max(MIN_NARRATOR_WIDTH_PX, width - channelWidth - handleWidth - MIN_CONVERSATION_WIDTH_PX)
        : MIN_NARRATOR_WIDTH_PX;
    return { minimum: MIN_NARRATOR_WIDTH_PX, maximum };
}

function setNarratorWidth(requestedWidth, persist = true) {
    if (!narratorSidebar) return null;
    const bounds = getNarratorResizeBounds();
    const numericWidth = Number(requestedWidth);
    const width = Math.round(Math.min(bounds.maximum, Math.max(
        bounds.minimum,
        Number.isFinite(numericWidth) ? numericWidth : currentNarratorWidth()
    )));
    narratorSidebar.style.setProperty("--narrator-sidebar-width", width + "px");
    if (narratorResizeHandle) {
        narratorResizeHandle.setAttribute("aria-valuemin", String(bounds.minimum));
        narratorResizeHandle.setAttribute("aria-valuemax", String(Math.round(bounds.maximum)));
        narratorResizeHandle.setAttribute("aria-valuenow", String(width));
    }
    if (persist) {
        try {
            localStorage.setItem(NARRATOR_WIDTH_STORAGE_KEY, String(width));
        } catch (e) {
            console.warn("Could not persist narrator width:", e);
        }
    }
    return width;
}

function beginNarratorResize(event) {
    if (!narratorSidebar || !narratorResizeHandle) return;
    if (typeof event.button === "number" && event.button !== 0) return;
    narratorResizeState = {
        pointerId: event.pointerId,
        startX: Number(event.clientX || 0),
        startWidth: currentNarratorWidth()
    };
    document.body.classList.add("is-resizing-narrator");
    if (typeof narratorResizeHandle.setPointerCapture === "function" && event.pointerId != null) {
        narratorResizeHandle.setPointerCapture(event.pointerId);
    }
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function resizeNarratorFromPointer(event) {
    if (!narratorResizeState) return;
    if (
        narratorResizeState.pointerId != null
        && event.pointerId != null
        && event.pointerId !== narratorResizeState.pointerId
    ) return;
    // The narrator is right-aligned: moving its left edge left makes it wider.
    const delta = narratorResizeState.startX - Number(event.clientX || 0);
    setNarratorWidth(narratorResizeState.startWidth + delta, false);
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function finishNarratorResize(event = {}) {
    if (!narratorResizeState) return;
    if (
        narratorResizeState.pointerId != null
        && event.pointerId != null
        && event.pointerId !== narratorResizeState.pointerId
    ) return;
    narratorResizeState = null;
    document.body.classList.remove("is-resizing-narrator");
    setNarratorWidth(currentNarratorWidth(), true);
}

function resizeNarratorFromKeyboard(event) {
    if (!narratorSidebar) return;
    const bounds = getNarratorResizeBounds();
    let nextWidth = null;
    if (event.key === "ArrowLeft") nextWidth = currentNarratorWidth() + NARRATOR_KEYBOARD_STEP_PX;
    if (event.key === "ArrowRight") nextWidth = currentNarratorWidth() - NARRATOR_KEYBOARD_STEP_PX;
    if (event.key === "Home") nextWidth = bounds.minimum;
    if (event.key === "End") nextWidth = bounds.maximum;
    if (nextWidth === null) return;
    setNarratorWidth(nextWidth, true);
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function initNarratorResizer() {
    if (!narratorResizeHandle || !narratorSidebar) return;
    if (narratorResizeHandle.dataset.resizeInitialized === "true") return;
    narratorResizeHandle.dataset.resizeInitialized = "true";
    narratorResizeHandle.addEventListener("pointerdown", beginNarratorResize);
    narratorResizeHandle.addEventListener("keydown", resizeNarratorFromKeyboard);
    document.addEventListener("pointermove", resizeNarratorFromPointer);
    document.addEventListener("pointerup", finishNarratorResize);
    document.addEventListener("pointercancel", finishNarratorResize);
    if (typeof window.addEventListener === "function") {
        window.addEventListener("resize", () => setNarratorWidth(currentNarratorWidth(), false));
    }
    try {
        const savedWidth = Number(localStorage.getItem(NARRATOR_WIDTH_STORAGE_KEY));
        setNarratorWidth(savedWidth > 0 ? savedWidth : currentNarratorWidth(), false);
    } catch (e) {
        setNarratorWidth(currentNarratorWidth(), false);
    }
}

function currentChannelsWidth() {
    if (!channelsSidebar) return MIN_CHANNELS_WIDTH_PX;
    const inlineWidth = parseFloat(
        channelsSidebar.style && channelsSidebar.style.getPropertyValue
            ? channelsSidebar.style.getPropertyValue("--channels-sidebar-width")
            : ""
    );
    return Number.isFinite(inlineWidth) && inlineWidth > 0
        ? inlineWidth
        : (elementWidth(channelsSidebar) || 300);
}

function setChannelsWidth(requestedWidth, persist = true) {
    if (!channelsSidebar) return null;
    const numericWidth = Number(requestedWidth);
    const width = Math.round(Math.min(MAX_CHANNELS_WIDTH_PX, Math.max(
        MIN_CHANNELS_WIDTH_PX,
        Number.isFinite(numericWidth) ? numericWidth : currentChannelsWidth()
    )));
    channelsSidebar.style.setProperty("--channels-sidebar-width", width + "px");
    if (channelsResizeHandle) {
        channelsResizeHandle.setAttribute("aria-valuemin", String(MIN_CHANNELS_WIDTH_PX));
        channelsResizeHandle.setAttribute("aria-valuemax", String(MAX_CHANNELS_WIDTH_PX));
        channelsResizeHandle.setAttribute("aria-valuenow", String(width));
    }
    if (persist) {
        try {
            localStorage.setItem(CHANNELS_WIDTH_STORAGE_KEY, String(width));
        } catch (e) {
            console.warn("Could not persist channel sidebar width:", e);
        }
    }
    return width;
}

function beginChannelsResize(event) {
    if (!channelsSidebar || !channelsResizeHandle) return;
    if (typeof event.button === "number" && event.button !== 0) return;
    channelsResizeState = {
        pointerId: event.pointerId,
        startX: Number(event.clientX || 0),
        startWidth: currentChannelsWidth()
    };
    document.body.classList.add("is-resizing-channels");
    if (typeof channelsResizeHandle.setPointerCapture === "function" && event.pointerId != null) {
        channelsResizeHandle.setPointerCapture(event.pointerId);
    }
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function resizeChannelsFromPointer(event) {
    if (!channelsResizeState) return;
    if (
        channelsResizeState.pointerId != null
        && event.pointerId != null
        && event.pointerId !== channelsResizeState.pointerId
    ) return;
    const delta = Number(event.clientX || 0) - channelsResizeState.startX;
    setChannelsWidth(channelsResizeState.startWidth + delta, false);
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function finishChannelsResize(event = {}) {
    if (!channelsResizeState) return;
    if (
        channelsResizeState.pointerId != null
        && event.pointerId != null
        && event.pointerId !== channelsResizeState.pointerId
    ) return;
    channelsResizeState = null;
    document.body.classList.remove("is-resizing-channels");
    setChannelsWidth(currentChannelsWidth(), true);
}

function resizeChannelsFromKeyboard(event) {
    let nextWidth = null;
    if (event.key === "ArrowLeft") nextWidth = currentChannelsWidth() - CHANNELS_KEYBOARD_STEP_PX;
    if (event.key === "ArrowRight") nextWidth = currentChannelsWidth() + CHANNELS_KEYBOARD_STEP_PX;
    if (event.key === "Home") nextWidth = MIN_CHANNELS_WIDTH_PX;
    if (event.key === "End") nextWidth = MAX_CHANNELS_WIDTH_PX;
    if (nextWidth === null) return;
    setChannelsWidth(nextWidth, true);
    if (typeof event.preventDefault === "function") event.preventDefault();
}

function setChannelsSidebarOpen(isOpen, persist = true) {
    if (!channelsSidebar) return;
    channelsSidebar.classList.toggle("collapsed", !isOpen);
    channelsSidebar.classList.toggle("mobile-open", isOpen);
    if (btnToggleSidebar) {
        btnToggleSidebar.setAttribute("aria-expanded", String(isOpen));
        const icon = btnToggleSidebar.querySelector(".material-symbols-rounded");
        if (icon) icon.innerText = isOpen ? "left_panel_close" : "left_panel_open";
    }
    if (persist) {
        try {
            localStorage.setItem(CHANNELS_OPEN_STORAGE_KEY, String(isOpen));
        } catch (e) {
            console.warn("Could not persist channel sidebar state:", e);
        }
    }
}

function isChannelsSidebarOpen() {
    const mobileViewport = viewportWidth() > 0 && viewportWidth() <= 768;
    return mobileViewport
        ? channelsSidebar.classList.contains("mobile-open")
        : !channelsSidebar.classList.contains("collapsed");
}

function toggleChannelsSidebar() {
    setChannelsSidebarOpen(!isChannelsSidebarOpen());
}

function initChannelsSidebar() {
    if (!channelsSidebar) return;
    if (btnToggleSidebar && btnToggleSidebar.dataset.sidebarInitialized !== "true") {
        btnToggleSidebar.dataset.sidebarInitialized = "true";
        btnToggleSidebar.addEventListener("click", toggleChannelsSidebar);
    }
    if (btnCollapseChannels && btnCollapseChannels.dataset.sidebarInitialized !== "true") {
        btnCollapseChannels.dataset.sidebarInitialized = "true";
        btnCollapseChannels.addEventListener("click", () => setChannelsSidebarOpen(false));
    }
    if (channelsResizeHandle && channelsResizeHandle.dataset.resizeInitialized !== "true") {
        channelsResizeHandle.dataset.resizeInitialized = "true";
        channelsResizeHandle.addEventListener("pointerdown", beginChannelsResize);
        channelsResizeHandle.addEventListener("keydown", resizeChannelsFromKeyboard);
        document.addEventListener("pointermove", resizeChannelsFromPointer);
        document.addEventListener("pointerup", finishChannelsResize);
        document.addEventListener("pointercancel", finishChannelsResize);
    }
    try {
        const savedWidth = Number(localStorage.getItem(CHANNELS_WIDTH_STORAGE_KEY));
        setChannelsWidth(savedWidth > 0 ? savedWidth : currentChannelsWidth(), false);
        const storedOpen = localStorage.getItem(CHANNELS_OPEN_STORAGE_KEY);
        const mobileViewport = viewportWidth() > 0 && viewportWidth() <= 768;
        setChannelsSidebarOpen(storedOpen === null ? !mobileViewport : storedOpen !== "false", false);
    } catch (e) {
        setChannelsWidth(currentChannelsWidth(), false);
        setChannelsSidebarOpen(true, false);
    }
}

function initSSEEventSource() {
    if (typeof EventSource === "undefined") return;
    try {
        const es = new EventSource("/api/events");
        es.onopen = () => {
            window.sseConnected = true;
        };
        es.onerror = () => {
            window.sseConnected = false;
        };
        es.addEventListener("message", (evt) => {
            try {
                const data = JSON.parse(evt.data);
                if (data && data.room_id) {
                    loadRooms();
                    if (data.room_id === activeRoomId) {
                        loadHistory();
                    }
                }
            } catch (e) {}
        });
        es.addEventListener("room_changed", () => {
            loadRooms();
            fetchAttentionQueue();
        });
        es.addEventListener("prewarm_ready", (evt) => {
            try {
                const data = JSON.parse(evt.data);
                if (data && data.room_id) {
                    fetchAttentionQueue();
                    if (data.room_id === activeRoomId) {
                        handleGenerateDigest();
                    }
                }
            } catch (e) {}
        });
    } catch (e) {
        window.sseConnected = false;
    }
}

// Initialize application
function init() {
    applySettings();
    initVoiceModeControl();
    initSettingsModal();
    initComposerResizer();
    initNarratorResizer();
    initChannelsSidebar();
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
    initSSEEventSource();

    // Always keep a snappy baseline refresh, independent of event transport.
    // SSE / webhooks make updates immediate when available; these timers remain
    // the honest fallback when DDP/webhooks are off or degraded.
    // Active conversation ~4s; channel rail / attention ~7s.
    setInterval(checkStatus, 5000);
    setInterval(loadHistory, 4000);
    setInterval(() => {
        fetchAttentionQueue();
        loadRooms();
    }, 7000);

    // Bind Event Listeners
    btnGenerateDigest.addEventListener("click", handleGenerateDigest);
    btnPlayPause.addEventListener("click", handlePlayPause);
    btnStop.addEventListener("click", handleStop);
    speedRange.addEventListener("input", handleSpeedChange);
    if (digestContent) {
        digestContent.addEventListener("input", handleNarrationEdit);
    }
    updateNarrationPlayState();
    btnMic.addEventListener("click", toggleSpeechInput);
    bindMessageComposer();
    initAgentTargeting();

    if (commandInput) {
        commandInput.addEventListener("input", (e) => {
            syncCommandDraftState(e.target.value);
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

    bindQuickSuggestionControls();
    bindCopyMessageControls();
    bindJumpToLatestMessageControl();
}

function getLatestConversationMessage() {
    if (!transcriptFeed) return null;
    const messages = transcriptFeed.querySelectorAll(".chat-msg");
    return messages.length ? messages[messages.length - 1] : null;
}

function captureTranscriptReadingAnchor() {
    if (!transcriptFeed) return null;
    const scrollTop = Number(transcriptFeed.scrollTop) || 0;
    const nodes = transcriptFeed.querySelectorAll(".chat-msg[data-id], .system-event-chip[data-id]");
    for (const node of nodes) {
        const top = Number(node.offsetTop) || 0;
        const height = Number(node.offsetHeight) || 0;
        if (top + height > scrollTop + 1) {
            return {
                scrollTop,
                anchorId: node.getAttribute("data-id") || "",
                offsetWithin: scrollTop - top,
            };
        }
    }
    return { scrollTop, anchorId: "", offsetWithin: 0 };
}

function restoreTranscriptReadingAnchor(anchor) {
    if (!transcriptFeed || !anchor) return;
    if (anchor.anchorId) {
        const safeId = (window.CSS && typeof window.CSS.escape === "function")
            ? window.CSS.escape(anchor.anchorId)
            : String(anchor.anchorId).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        const node = transcriptFeed.querySelector(`[data-id="${safeId}"]`);
        if (node) {
            transcriptFeed.scrollTop = Math.max(0, (Number(node.offsetTop) || 0) + (Number(anchor.offsetWithin) || 0));
            return;
        }
    }
    transcriptFeed.scrollTop = Math.max(0, Number(anchor.scrollTop) || 0);
}

function transcriptRenderSignature(messages, state) {
    const loadFlag = state
        ? `${Boolean(state.hasMoreOlder)}:${Boolean(state.loadingOlder)}`
        : "none";
    const parts = (messages || []).map((msg) => {
        const event = msg && msg.event && typeof msg.event === "object" ? msg.event : {};
        return [
            msg.id || "",
            msg.timestamp || "",
            msg.lane || "",
            msg.name || msg.username || "",
            (msg.text || "").length,
            event.kind || "",
            event.response_time_seconds ?? "",
            event.agent || "",
        ].join("|");
    });
    return `${loadFlag}::${parts.join(";")}`;
}

function updateJumpToLatestButton() {
    if (!btnJumpToLatest || !transcriptFeed) return;

    const latestMessage = getLatestConversationMessage();
    if (!latestMessage) {
        btnJumpToLatest.hidden = true;
        return;
    }

    // The control is useful only while the latest message begins below the
    // visible conversation viewport; avoid covering content once Ed reaches it.
    // Compare against the feed's own scroll metrics only — never auto-scroll.
    const latestMessageTop = Number(latestMessage.offsetTop) || 0;
    const scrollTop = Number(transcriptFeed.scrollTop) || 0;
    btnJumpToLatest.hidden = latestMessageTop <= scrollTop + 8;
}

function jumpToLatestMessage() {
    // Explicit user action only. Polling must never call this.
    const latestMessage = getLatestConversationMessage();
    if (!latestMessage || !transcriptFeed) return;

    const top = Math.max(0, Number(latestMessage.offsetTop) || 0);
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (typeof transcriptFeed.scrollTo === "function") {
        transcriptFeed.scrollTo({ top, behavior: reduceMotion ? "auto" : "smooth" });
    } else {
        transcriptFeed.scrollTop = top;
    }
    updateJumpToLatestButton();
}

function bindJumpToLatestMessageControl() {
    if (!transcriptFeed || transcriptFeed.dataset.jumpToLatestBound === "true") return;
    transcriptFeed.dataset.jumpToLatestBound = "true";
    transcriptFeed.addEventListener("scroll", updateJumpToLatestButton, { passive: true });

    if (btnJumpToLatest) {
        btnJumpToLatest.addEventListener("click", jumpToLatestMessage);
    }
}

function copyTextToClipboard(text, btnElement) {
    if (!text) return;
    let copied = false;
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => {
                showCopySuccess(btnElement);
            }).catch(() => {
                fallbackCopyText(text, btnElement);
            });
            return;
        }
    } catch (e) {
        console.warn("navigator.clipboard.writeText failed, fallback used:", e);
    }
    fallbackCopyText(text, btnElement);
}

function fallbackCopyText(text, btnElement) {
    let copied = false;
    try {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        textarea.style.top = "-9999px";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        copied = document.execCommand("copy");
        document.body.removeChild(textarea);
    } catch (err) {
        console.error("Fallback copy failed:", err);
    }
    if (copied) {
        showCopySuccess(btnElement);
    }
}

function showCopySuccess(btnElement) {
    if (!btnElement) return;
    const iconEl = btnElement.querySelector(".material-symbols-rounded");
    const originalIcon = iconEl ? iconEl.textContent : "content_copy";
    const originalTitle = btnElement.getAttribute("title") || "Copy message text";

    if (iconEl) iconEl.textContent = "check";
    btnElement.setAttribute("title", "Copied!");
    btnElement.classList.add("copied");

    setTimeout(() => {
        if (iconEl) iconEl.textContent = originalIcon;
        btnElement.setAttribute("title", originalTitle);
        btnElement.classList.remove("copied");
    }, 2000);
}

function bindCopyMessageControls() {
    if (transcriptFeed && transcriptFeed.dataset.copyListenerBound !== "true") {
        transcriptFeed.dataset.copyListenerBound = "true";
        transcriptFeed.addEventListener("click", event => {
            const copyBtn = event.target.closest(".btn-copy-msg");
            if (copyBtn) {
                event.preventDefault();
                event.stopPropagation();
                const textToCopy = copyBtn.getAttribute("data-raw-text") || "";
                copyTextToClipboard(textToCopy, copyBtn);
            }
        });
    }
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
        window.ddpConnected = data.ddp && data.ddp.state === "connected";

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

            await fetchAttentionQueue(true);
            renderChannelsList();
            updateHeaderRoomInfo();
            await fetchAgentModels(activeRoomId, getRoomName(activeRoomId));
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
    renderSmartQuickSuggestionButtons(state.smartQuickSuggestions);
    updateNarrationPlayState();

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

    const targetRoom = roomsList.find(r => r.id === roomId);
    if (targetRoom) {
        targetRoom.has_unread = false;
        targetRoom.unread_count = 0;
    }
    fetch("/api/read_cursor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room_id: roomId })
    }).catch(err => console.debug("Read cursor update error:", err));

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

function getRoomName(roomId) {
    const room = roomsList.find(item => item.id === roomId);
    return room ? room.name : null;
}

// Bind Event Listeners
btnGenerateDigest.addEventListener("click", handleGenerateDigest);
btnPlayPause.addEventListener("click", handlePlayPause);
btnStop.addEventListener("click", handleStop);
speedRange.addEventListener("input", handleSpeedChange);
if (digestContent) {
    digestContent.addEventListener("input", handleNarrationEdit);
}
updateNarrationPlayState();
    btnMic.addEventListener("click", toggleSpeechInput);
    bindMessageComposer();
    bindQuickSuggestionControls();

// Attention modal listeners
const btnCloseAttn = document.getElementById("btn-close-attention-modal");
const btnCancelAttn = document.getElementById("btn-cancel-attention-modal");
const btnSaveAttn = document.getElementById("btn-save-attention-modal");
if (btnCloseAttn) btnCloseAttn.addEventListener("click", closeAttentionSettingsModal);
if (btnCancelAttn) btnCancelAttn.addEventListener("click", closeAttentionSettingsModal);
if (btnSaveAttn) btnSaveAttn.addEventListener("click", saveAttentionSettings);

const voiceCbElem = document.getElementById("attn-voice-active");
const narrationCbElem = document.getElementById("attn-narration-active");
if (voiceCbElem && narrationCbElem) {
    voiceCbElem.addEventListener("change", () => {
        if (voiceCbElem.checked) {
            narrationCbElem.checked = true;
        }
    });
}

initAgentTargeting();

const btnCloseModelModal = document.getElementById("btn-close-agent-model-modal");
const btnCancelModelModal = document.getElementById("btn-cancel-agent-model");
const btnApplyModelModal = document.getElementById("btn-apply-agent-model");
const btnResetModelModal = document.getElementById("btn-reset-agent-model");
const agentModelSelect = document.getElementById("model-select-input");

if (btnCloseModelModal) btnCloseModelModal.addEventListener("click", closeAgentModelModal);
if (btnCancelModelModal) btnCancelModelModal.addEventListener("click", closeAgentModelModal);
if (btnApplyModelModal) btnApplyModelModal.addEventListener("click", () => applyAgentModelSwitch(false));
if (btnResetModelModal) btnResetModelModal.addEventListener("click", () => applyAgentModelSwitch(true));
if (agentModelSelect) agentModelSelect.addEventListener("change", () => {
    const agentInput = document.getElementById("model-target-agent");
    const agent = agentInput ? agentInput.value : "codex";
    const info = currentAgentModelsData && currentAgentModelsData.agents
        ? currentAgentModelsData.agents[agent]
        : null;
    renderAgentEffortOptions(info, agentModelSelect.value);
});

if (commandInput) {
    commandInput.addEventListener("input", (e) => {
        syncCommandDraftState(e.target.value);
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

let attentionQueueData = [];
let pendingAttentionQueueData = null;
let queueHasPendingUpdate = false;

function formatElapsedSeconds(sec) {
    if (sec == null || isNaN(sec)) return "";
    const totalSec = Math.max(0, Math.floor(sec));
    if (totalSec < 60) return `${totalSec}s`;
    const mins = Math.floor(totalSec / 60);
    if (mins < 60) return `${mins}m`;
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hrs}h ${remMins}m`;
}

function isMidTurnActive() {
    const rawInput = commandInput ? commandInput.value.trim() : "";
    return rawInput.length > 0;
}

async function fetchAttentionQueue(forceRender = false) {
    try {
        const resp = await fetch("/api/attention/queue");
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.success && Array.isArray(data.queue)) {
            if (!forceRender && isMidTurnActive()) {
                pendingAttentionQueueData = data.queue;
                queueHasPendingUpdate = true;
                showQueueUpdateNotice();
            } else {
                attentionQueueData = data.queue;
                pendingAttentionQueueData = null;
                queueHasPendingUpdate = false;
                hideQueueUpdateNotice();
                renderChannelsList(channelSearchInput ? channelSearchInput.value : "");
            }
        }
    } catch (err) {
        console.warn("Could not fetch attention queue:", err);
    }
}

function showQueueUpdateNotice() {
    let noticeEl = document.getElementById("queue-update-notice");
    if (!noticeEl && channelsListEl && channelsListEl.parentElement) {
        noticeEl = document.createElement("div");
        noticeEl.id = "queue-update-notice";
        noticeEl.className = "queue-update-notice";
        noticeEl.innerHTML = `
            <span>Queue updated</span>
            <span class="material-symbols-rounded" style="font-size:16px;">refresh</span>
        `;
        noticeEl.addEventListener("click", () => {
            applyPendingAttentionQueueIfReady(true);
        });
        channelsListEl.parentElement.insertBefore(noticeEl, channelsListEl);
    }
    if (noticeEl) noticeEl.style.display = "flex";
}

function hideQueueUpdateNotice() {
    const noticeEl = document.getElementById("queue-update-notice");
    if (noticeEl) noticeEl.style.display = "none";
}

function applyPendingAttentionQueueIfReady(force = false) {
    if (!queueHasPendingUpdate || !Array.isArray(pendingAttentionQueueData)) {
        return false;
    }
    if (!force && isMidTurnActive()) {
        return false;
    }

    attentionQueueData = pendingAttentionQueueData;
    pendingAttentionQueueData = null;
    queueHasPendingUpdate = false;
    hideQueueUpdateNotice();
    renderChannelsList(channelSearchInput ? channelSearchInput.value : "");
    return true;
}

let currentModalSnoozedUntil = null;

// Attention Settings Modal Logic
async function openAttentionSettingsModal(channelName) {
    const modal = document.getElementById("attention-settings-modal");
    const titleEl = document.getElementById("attention-modal-channel-title");
    const channelInput = document.getElementById("attn-channel-name");
    const activeCb = document.getElementById("attn-active");
    const impSelect = document.getElementById("attn-base-importance");
    const urgSelect = document.getElementById("attn-urgency");
    const blockCb = document.getElementById("attn-blocking");
    const boostCb = document.getElementById("attn-focus-today");
    const snoozeSelect = document.getElementById("attn-snooze-select");
    const keepOption = document.getElementById("attn-snooze-keep-option");
    const statusText = document.getElementById("attn-snooze-status-text");
    const deadlineInput = document.getElementById("attn-deadline");

    const visibleCb = document.getElementById("attn-visible");
    const narrationCb = document.getElementById("attn-narration-active");
    const voiceCb = document.getElementById("attn-voice-active");

    if (!modal) return;
    if (titleEl) titleEl.innerText = `Channel Attention Settings: #${channelName}`;
    if (channelInput) {
        channelInput.value = channelName;
        channelInput.dataset.canonicalName = channelName;
    }
    currentModalSnoozedUntil = null;

    // Reset default form state
    if (activeCb) activeCb.checked = true;
    if (visibleCb) visibleCb.checked = true;
    if (narrationCb) narrationCb.checked = false;
    if (voiceCb) voiceCb.checked = false;
    if (impSelect) impSelect.value = "3";
    if (urgSelect) urgSelect.value = "normal";
    if (blockCb) blockCb.checked = false;
    if (boostCb) boostCb.checked = false;
    if (snoozeSelect) snoozeSelect.value = "none";
    if (keepOption) keepOption.style.display = "none";
    if (statusText) statusText.style.display = "none";
    if (deadlineInput) deadlineInput.value = "";

    try {
        const resp = await fetch("/api/attention/config");
        if (resp.ok) {
            const data = await resp.json();
            const channels = (data && data.config && data.config.channels) ? data.config.channels : {};

            // Case-fold lookup
            const matchedKey = Object.keys(channels).find(k => k.toLowerCase() === channelName.toLowerCase());
            if (matchedKey && channels[matchedKey]) {
                const entry = channels[matchedKey];
                if (channelInput) channelInput.dataset.canonicalName = matchedKey;
                if (activeCb) activeCb.checked = entry.attention_active !== false;
                if (visibleCb) visibleCb.checked = entry.visible !== false;
                if (narrationCb) narrationCb.checked = !!entry.narration_active;
                if (voiceCb) voiceCb.checked = !!entry.voice_active;
                if (impSelect) impSelect.value = String(entry.base_importance || 3);
                if (urgSelect) urgSelect.value = entry.urgency || "normal";
                if (blockCb) blockCb.checked = !!entry.blocking;
                if (boostCb) boostCb.checked = !!entry.temporary_boost_until;

                // Snooze retention
                if (entry.snoozed_until) {
                    const dt = new Date(entry.snoozed_until);
                    if (dt.getTime() > Date.now()) {
                        currentModalSnoozedUntil = entry.snoozed_until;
                        if (keepOption) {
                            keepOption.style.display = "block";
                            keepOption.innerText = `Keep Current Snooze (until ${dt.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})})`;
                        }
                        if (snoozeSelect) snoozeSelect.value = "keep";
                        if (statusText) {
                            statusText.style.display = "block";
                            statusText.innerText = `Currently snoozed until ${dt.toLocaleString()}`;
                        }
                    } else if (snoozeSelect) {
                        snoozeSelect.value = "none";
                    }
                } else if (snoozeSelect) {
                    snoozeSelect.value = "none";
                }

                if (deadlineInput && entry.deadline) {
                    try {
                        const dtd = new Date(entry.deadline);
                        deadlineInput.value = dtd.toISOString().slice(0, 16);
                    } catch (e) {}
                }
            }
        }
    } catch (err) {
        console.warn("Could not fetch attention config for modal:", err);
    }

    modal.style.display = "flex";
}

function closeAttentionSettingsModal() {
    const modal = document.getElementById("attention-settings-modal");
    if (modal) modal.style.display = "none";
}

async function saveAttentionSettings() {
    const channelInput = document.getElementById("attn-channel-name");
    const channelName = channelInput ? (channelInput.dataset.canonicalName || channelInput.value) : "";
    if (!channelName) return;

    const activeCb = document.getElementById("attn-active");
    const visibleCb = document.getElementById("attn-visible");
    const narrationCb = document.getElementById("attn-narration-active");
    const voiceCb = document.getElementById("attn-voice-active");
    const impSelect = document.getElementById("attn-base-importance");
    const urgSelect = document.getElementById("attn-urgency");
    const blockCb = document.getElementById("attn-blocking");
    const boostCb = document.getElementById("attn-focus-today");
    const snoozeSelect = document.getElementById("attn-snooze-select");
    const deadlineInput = document.getElementById("attn-deadline");

    const now = new Date();
    let boostUntil = null;
    if (boostCb && boostCb.checked) {
        const endOfDay = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 23, 59, 59));
        boostUntil = endOfDay.toISOString();
    }

    let snoozedUntil = null;
    const snoozeVal = snoozeSelect ? snoozeSelect.value : "none";
    if (snoozeVal === "keep") {
        snoozedUntil = currentModalSnoozedUntil;
    } else if (snoozeVal === "1h") {
        snoozedUntil = new Date(now.getTime() + 3600 * 1000).toISOString();
    } else if (snoozeVal === "4h") {
        snoozedUntil = new Date(now.getTime() + 4 * 3600 * 1000).toISOString();
    } else if (snoozeVal === "24h") {
        snoozedUntil = new Date(now.getTime() + 24 * 3600 * 1000).toISOString();
    }

    let deadlineVal = null;
    if (deadlineInput && deadlineInput.value) {
        deadlineVal = new Date(deadlineInput.value).toISOString();
    }

    const payload = {
        channel_name: channelName,
        entry: {
            attention_active: activeCb ? activeCb.checked : true,
            visible: visibleCb ? visibleCb.checked : true,
            narration_active: narrationCb ? narrationCb.checked : false,
            voice_active: voiceCb ? voiceCb.checked : false,
            base_importance: impSelect ? parseInt(impSelect.value, 10) : 3,
            urgency: urgSelect ? urgSelect.value : "normal",
            blocking: blockCb ? blockCb.checked : false,
            temporary_boost_until: boostUntil,
            snoozed_until: snoozedUntil,
            deadline: deadlineVal,
        }
    };

    try {
        const resp = await fetch("/api/attention/config", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const errData = await resp.json();
            alert(`Failed to save settings: ${errData.detail || "Server error"}`);
            return;
        }
        closeAttentionSettingsModal();
        fetchAttentionQueue(true);
    } catch (err) {
        alert(`Error saving attention settings: ${err.message}`);
    }
}

function renderChannelsList(filterText = "") {
    if (!channelsListEl) return;

    const term = (filterText || "").trim().toLowerCase();

    // Map attention queue item by canonical channel name (lowercase)
    const queueMap = new Map();
    (attentionQueueData || []).forEach(q => {
        if (q && q.channel_name) {
            queueMap.set(q.channel_name.toLowerCase(), q);
        }
    });

    const activityTimestamp = (room, queueItem) => {
        const raw = queueItem && queueItem.last_activity_at != null
            ? queueItem.last_activity_at
            : (room._updatedAt || room.lm || room.updatedAt || 0);
        if (typeof raw === "number") return raw > 1e12 ? raw : raw * 1000;
        const parsed = new Date(raw || 0).getTime();
        return Number.isFinite(parsed) ? parsed : 0;
    };

    const isConfigured = queueItem => Boolean(
        queueItem && (
            queueItem.configured === true
            || (
                queueItem.configured == null
                && queueItem.queue_category !== "unconfigured"
                && queueItem.status !== "unconfigured"
            )
        )
    );

    const sortedRooms = [...roomsList].sort((a, b) => {
        const qa = queueMap.get((a.name || "").toLowerCase());
        const qb = queueMap.get((b.name || "").toLowerCase());
        const configuredA = isConfigured(qa);
        const configuredB = isConfigured(qb);
        if (configuredA !== configuredB) return configuredA ? -1 : 1;

        const scoreA = qa && qa.score != null && Number.isFinite(Number(qa.score)) ? Number(qa.score) : null;
        const scoreB = qb && qb.score != null && Number.isFinite(Number(qb.score)) ? Number(qb.score) : null;
        if (scoreA !== null || scoreB !== null) {
            if (scoreA === null) return 1;
            if (scoreB === null) return -1;
            if (scoreA !== scoreB) return scoreB - scoreA;
        }

        const timeDifference = activityTimestamp(b, qb) - activityTimestamp(a, qa);
        if (timeDifference !== 0) return timeDifference;
        return (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" });
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
        const qitem = queueMap.get((room.name || "").toLowerCase());
        const hasUnread = Boolean((qitem && qitem.has_unread) || room.has_unread);
        const unreadClass = hasUnread ? "has-unread" : "";
        const unreadBadgeHTML = hasUnread ? `<span class="unread-badge" title="Unseen real agent reply in this channel">New</span>` : "";

        let badgeHTML = "";

        if (qitem) {
            const category = qitem.queue_category;
            if (category === "busy") {
                const elapsedStr = formatElapsedSeconds(qitem.working_elapsed_seconds);
                badgeHTML = `<span class="attn-badge attn-badge-busy" title="Agent is currently working">Busy ${elapsedStr}</span>`;
            } else if (category === "ranked") {
                const attentionState = String(qitem.attention_state || "needs_review").toLowerCase();
                const statusClass = attentionState === "needs_help"
                    ? "attn-badge-needs-help"
                    : attentionState === "needs_decision"
                        ? "attn-badge-needs-decision"
                        : "attn-badge-ready";
                const statusTitle = attentionState === "needs_help"
                    ? "Agent reported a problem"
                    : attentionState === "needs_decision"
                        ? "Your decision is needed"
                        : "Response ready to review";
                badgeHTML = `<span class="attn-badge ${statusClass}" title="${statusTitle}" aria-label="${statusTitle}"><span class="attn-ready-dot" aria-hidden="true"></span></span>`;
            } else if (category === "snoozed") {
                badgeHTML = `<span class="attn-badge attn-badge-snoozed" title="Snoozed">Snoozed</span>`;
            } else if (category === "unconfigured") {
                badgeHTML = `<span class="attn-badge attn-badge-unconfigured" title="Unconfigured">Unconfigured</span>`;
            } else if (category === "unknown") {
                badgeHTML = `<span class="attn-badge attn-badge-unknown" title="Unwatched room">?</span>`;
            } else if (category === "idle") {
                badgeHTML = `<span class="attn-badge attn-badge-idle" title="Idle">Idle</span>`;
            } else if (category === "inactive") {
                badgeHTML = `<span class="attn-badge attn-badge-inactive" title="Inactive">Inactive</span>`;
            }
        } else {
            badgeHTML = `<span class="attn-badge attn-badge-unknown" title="Unwatched room">?</span>`;
        }

        return `
            <div class="channel-item ${activeClass} ${unreadClass}" data-room-id="${room.id}" data-channel-name="${escapeHTML(room.name)}" role="button" tabindex="0">
                <span class="material-symbols-rounded channel-icon">tag</span>
                <div class="channel-info">
                    <span class="channel-name">${escapeHTML(room.name)}</span>
                </div>
                <div class="channel-badges">
                    ${unreadBadgeHTML}
                    ${badgeHTML}
                    <button class="btn-tune-channel" data-channel-name="${escapeHTML(room.name)}" title="Edit Channel Attention Settings">
                        <span class="material-symbols-rounded" style="font-size:16px;">tune</span>
                    </button>
                </div>
            </div>
        `;
    }).join("");

    channelsListEl.querySelectorAll(".channel-item").forEach(item => {
        const handleSelect = (e) => {
            if (e && e.target && e.target.closest(".btn-tune-channel")) {
                return;
            }
            const rid = item.dataset.roomId;
            selectRoom(rid);
            if (channelsSidebar) channelsSidebar.classList.remove("mobile-open");
        };
        item.addEventListener("click", handleSelect);
        item.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                handleSelect(e);
            }
        });
    });

    channelsListEl.querySelectorAll(".btn-tune-channel").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const cname = btn.dataset.channelName;
            openAttentionSettingsModal(cname);
        });
    });
}

function handleRoomChange() {
    // Restore UI state of the new active room
    restoreRoomUIData(activeRoomId);
    applyPendingAttentionQueueIfReady();

    // Fetch active agent models for the selected room
    fetchAgentModels(activeRoomId, getRoomName(activeRoomId));

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

// Agent Model Configuration & State Management
let currentAgentModelsData = null;
let agentModelsFetchSeq = 0;

function clearComposerFeedback() {
    const feedbackEl = document.getElementById("send-feedback");
    if (!feedbackEl) return;
    feedbackEl.textContent = "";
    feedbackEl.className = "send-feedback hidden";
    feedbackEl.style.display = "none";
}

function setModelControlStatus(message, kind = "") {
    const status = document.getElementById("model-control-status");
    if (!status) return;
    status.innerText = message || "";
    status.className = `model-control-status${kind ? ` ${kind}` : ""}`;
}

function setAgentModelControlsLoading() {
    MODEL_CONTROL_AGENTS.forEach(agent => {
        const btn = document.getElementById(`btn-agent-${agent}`);
        const label = document.getElementById(`model-label-${agent}`);
        if (btn) {
            btn.classList.remove("model-unavailable");
            btn.title = `${formatAgentTitleJS(agent)} · Loading ACLI model…`;
        }
        if (label) label.innerText = "Loading…";
    });
}

async function fetchAgentModels(roomId, channelName) {
    const requestedRoomId = roomId || activeRoomId;
    const requestedChannelName = channelName || getRoomName(requestedRoomId);
    const requestSeq = ++agentModelsFetchSeq;
    if (!requestedRoomId || !requestedChannelName) return null;
    setAgentModelControlsLoading();
    try {
        const url = "/api/agent-models?"
            + `roomId=${encodeURIComponent(requestedRoomId)}`
            + `&channelName=${encodeURIComponent(requestedChannelName)}`;
        const resp = await fetch(url);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "ACLI model state is unavailable");
        if (requestSeq !== agentModelsFetchSeq || requestedRoomId !== activeRoomId) return null;
        if (data && data.success && data.agents) {
            currentAgentModelsData = data;
            updateAgentBadgesUI(data.agents);
            return data;
        }
        throw new Error("ACLI returned an incomplete model catalog");
    } catch (err) {
        if (requestSeq !== agentModelsFetchSeq || requestedRoomId !== activeRoomId) return null;
        currentAgentModelsData = null;
        MODEL_CONTROL_AGENTS.forEach(agent => {
            const btn = document.getElementById(`btn-agent-${agent}`);
            const label = document.getElementById(`model-label-${agent}`);
            if (btn) {
                btn.classList.add("model-unavailable");
                btn.title = `${formatAgentTitleJS(agent)} · Model state unavailable`;
            }
            if (label) label.innerText = "Unavailable";
        });
        console.warn("Could not fetch agent models:", err);
        return null;
    }
}

function updateAgentBadgesUI(agents) {
    if (!agents) return;
    MODEL_CONTROL_AGENTS.forEach(ag => {
        const info = agents[ag];
        if (!info) return;

        const btn = document.getElementById(`btn-agent-${ag}`);
        const label = document.getElementById(`model-label-${ag}`);

        if (btn) {
            btn.classList.toggle("model-unavailable", !info.available);
            btn.title = info.available
                ? (info.display_label || `${ag} · ${info.display_model} · ${info.display_effort}`)
                : `${formatAgentTitleJS(ag)} · Model state unavailable`;
        }
        if (label) {
            label.innerText = info.available
                ? `${info.display_model || "Default"} · ${info.display_effort || "low"}`
                : "Unavailable";
        }
    });
}

function formatAgentTitleJS(agentId) {
    const normalized = String(agentId || "").trim().toLowerCase();
    const map = {
        agy: "AGY"
    };
    return map[normalized] || (normalized ? normalized[0].toUpperCase() + normalized.slice(1) : "");
}

function formatModelTitleJS(modelId) {
    if (!modelId) return "Default";
    const map = {
        "gpt-5.6-sol": "GPT 5.6 Sol",
        "gpt-5.6-terra": "GPT 5.6 Terra",
        "gpt-5.6-luna": "GPT 5.6 Luna",
        "gpt-5.4": "GPT 5.4",
        "gpt-5.5": "GPT 5.5",
        "gpt-5.4-mini": "GPT 5.4 Mini",
        "gpt-5.3": "GPT 5.3",
        "gpt-5.2": "GPT 5.2",
        "gemini-3.6-flash": "Gemini 3.6 Flash",
        "gemini-3.5-flash": "Gemini 3.5 Flash",
        "gemini-3.1-pro": "Gemini 3.1 Pro",
        "gemini-3-flash-preview": "Gemini 3 Flash",
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
        "gemini-3-pro-preview": "Gemini 3 Pro",
        "gemini-2.5-pro": "Gemini 2.5 Pro",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "claude-sonnet-5": "Claude Sonnet 5",
        "claude-sonnet-4-6": "Claude Sonnet 4.6",
        "claude-opus-5": "Claude Opus 5",
        "claude-opus-4-8": "Claude Opus 4.8",
        "claude-opus-4-7": "Claude Opus 4.7",
        "claude-haiku-3-5": "Claude Haiku 3.5",
        "grok-4.5": "Grok 4.5",
        "grok-4": "Grok 4"
    };
    return map[modelId.toLowerCase()] || modelId;
}

function getAgentModelOptions(info) {
    if (info && Array.isArray(info.model_options) && info.model_options.length) {
        return info.model_options;
    }
    return ((info && info.available_models) || []).map(model => ({
        id: model,
        label: formatModelTitleJS(model),
        efforts: (info && info.available_efforts) || []
    }));
}

function renderAgentEffortOptions(info, modelId, preferredEffort = null) {
    const effortEl = document.getElementById("effort-select-input");
    if (!effortEl || !info) return;
    const option = getAgentModelOptions(info).find(item => item.id === modelId);
    const efforts = option && Array.isArray(option.efforts)
        ? option.efforts
        : (info.available_efforts || []);
    const requested = preferredEffort || effortEl.value || info.selector_effort || info.current_effort;
    const selectedEffort = efforts.includes(requested) ? requested : efforts[0];
    effortEl.innerHTML = efforts.map(effort => {
        const selected = effort === selectedEffort ? " selected" : "";
        return `<option value="${escapeHTML(effort)}"${selected}>${escapeHTML(effort)}</option>`;
    }).join("");
    effortEl.value = selectedEffort || "";
    effortEl.disabled = efforts.length === 0;
}

async function openAgentModelModal(agent) {
    const modal = document.getElementById("agent-model-modal");
    if (!modal) return;

    // A prior ordinary-message failure must not follow the user into the
    // separate model-control workflow.  Model control reports in its own
    // inline status element below the selectors.
    clearComposerFeedback();

    const targetAgent = (agent || "codex").toLowerCase();
    const targetAgentInput = document.getElementById("model-target-agent");
    if (targetAgentInput) targetAgentInput.value = targetAgent;

    const nameEl = document.getElementById("model-modal-agent-name");
    const summaryEl = document.getElementById("model-modal-active-summary");
    const selectEl = document.getElementById("model-select-input");
    const effortEl = document.getElementById("effort-select-input");
    const iconEl = document.getElementById("model-modal-agent-icon");
    const applyButton = document.getElementById("btn-apply-agent-model");
    const resetButton = document.getElementById("btn-reset-agent-model");

    modal.style.display = "flex";
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Loading ACLI models…</option>';
        selectEl.disabled = true;
    }
    if (effortEl) {
        effortEl.innerHTML = '<option value="">Loading efforts…</option>';
        effortEl.disabled = true;
    }
    if (applyButton) applyButton.disabled = true;
    if (resetButton) resetButton.disabled = true;
    setModelControlStatus(`Loading ${targetAgent}'s shared ACLI catalog…`);

    if (nameEl) nameEl.innerText = formatAgentTitleJS(targetAgent);

    const roomName = getRoomName(activeRoomId);
    const dataMatchesRoom = currentAgentModelsData
        && currentAgentModelsData.room_id === activeRoomId
        && currentAgentModelsData.channel_name
        && roomName
        && currentAgentModelsData.channel_name.toLowerCase() === roomName.toLowerCase();
    if (!dataMatchesRoom) {
        await fetchAgentModels(activeRoomId, roomName);
    }

    const info = (currentAgentModelsData && currentAgentModelsData.agents)
        ? currentAgentModelsData.agents[targetAgent]
        : null;
    if (!info || !info.available || !Array.isArray(info.available_models) || !info.available_models.length) {
        if (summaryEl) summaryEl.innerText = "Current model unavailable";
        setModelControlStatus(
            `No ACLI model catalog is available for ${targetAgent} in #${roomName || "this channel"}.`,
            "error"
        );
        return;
    }

    const currModel = info.current_model;
    const currEffort = info.selector_effort || info.current_effort;
    const dispModel = info.display_model;

    if (summaryEl) {
        summaryEl.innerText = `Current Model: ${dispModel} (${currEffort || 'low'})`;
    }

    if (iconEl) {
        iconEl.innerText = targetAgent.substring(0, 2).toUpperCase();
    }

    if (selectEl) {
        let optsHtml = "";
        const selectorModel = info.selector_model || currModel;
        getAgentModelOptions(info).forEach(option => {
            const isSel = (option.id.toLowerCase() === selectorModel.toLowerCase()) ? " selected" : "";
            optsHtml += `<option value="${escapeHTML(option.id)}"${isSel}>${escapeHTML(option.label || formatModelTitleJS(option.id))}</option>`;
        });
        selectEl.innerHTML = optsHtml;
        selectEl.value = selectorModel;
        selectEl.disabled = false;
    }

    renderAgentEffortOptions(info, info.selector_model || currModel, currEffort);
    if (applyButton) applyButton.disabled = false;
    if (resetButton) resetButton.disabled = false;
    setModelControlStatus("Models and all effort levels loaded from ACLI's shared configuration.");
}

function closeAgentModelModal() {
    const modal = document.getElementById("agent-model-modal");
    if (modal) modal.style.display = "none";
}

async function applyAgentModelSwitch(resetDefault = false) {
    const targetAgentInput = document.getElementById("model-target-agent");
    const agent = targetAgentInput ? targetAgentInput.value : "codex";
    const modelSelect = document.getElementById("model-select-input");
    const effortSelect = document.getElementById("effort-select-input");

    const targetModel = resetDefault ? "default" : (modelSelect ? modelSelect.value : "default");
    const targetEffort = effortSelect ? effortSelect.value : "high";
    const roomId = activeRoomId;
    const channelName = getRoomName(roomId);
    const applyButton = document.getElementById("btn-apply-agent-model");
    const resetButton = document.getElementById("btn-reset-agent-model");

    // Clear any stale composer banner before starting a model-control action.
    // The model command is sent by the typed endpoint, not by the composer.
    clearComposerFeedback();

    if (!roomId || !channelName || (!resetDefault && (!targetModel || !targetEffort))) {
        setModelControlStatus("The selected room or ACLI model catalog is unavailable.", "error");
        return;
    }

    if (applyButton) applyButton.disabled = true;
    if (resetButton) resetButton.disabled = true;
    setModelControlStatus("Validating the change with ACLI…");

    try {
        const prepResp = await fetch("/api/agent-models/prepare", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                room_id: roomId,
                channel_name: channelName,
                agent: agent,
                target_model: targetModel,
                target_effort: targetEffort
            })
        });

        if (!prepResp.ok) {
            const errData = await prepResp.json();
            throw new Error(errData.detail || "Invalid model change request");
        }

        const prepData = await prepResp.json();
        if (activeRoomId !== roomId) {
            throw new Error("The active channel changed before dispatch; no model command was sent")
        }
        const snapshot = prepData.confirmation_snapshot;
        const change = prepData.change || {};

        setModelControlStatus(`Sending ${change.formatted_command || snapshot.exact_message} to Rocket.Chat…`);
        const confirmResp = await fetch("/api/agent-models/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                confirmation_snapshot: snapshot,
                channel_name: channelName
            })
        });
        const confirmData = await confirmResp.json();
        if (!confirmResp.ok) {
            throw new Error(confirmData.detail || "Rocket.Chat rejected the model command");
        }

        await fetchAgentModels(roomId, channelName);
        clearComposerFeedback();
        if (confirmData.applied) {
            const effective = confirmData.effective || {};
            setModelControlStatus(
                `Applied: ${formatModelTitleJS(effective.current_model || change.target_model)} · ${effective.current_effort || change.target_effort}.`,
                "success"
            );
            const updated = currentAgentModelsData && currentAgentModelsData.agents
                ? currentAgentModelsData.agents[agent]
                : null;
            const summaryEl = document.getElementById("model-modal-active-summary");
            if (summaryEl && updated) {
                summaryEl.innerText = `Current Model: ${updated.display_model} (${updated.current_effort})`;
            }
        } else {
            setModelControlStatus(
                confirmData.message
                    || "The model command was sent to Rocket.Chat; ACLI is still finalizing the persisted change.",
                "pending"
            );
        }
    } catch (err) {
        console.error("Error applying model switch:", err);
        clearComposerFeedback();
        setModelControlStatus(`Model change failed: ${err.message}`, "error");
    } finally {
        if (applyButton) applyButton.disabled = false;
        if (resetButton) resetButton.disabled = false;
    }
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
    try {
        localStorage.setItem("vc_draft_" + targetRoomId, draft);
    } catch (e) {
        console.warn("Could not persist draft to localStorage:", e);
    }
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
    if (isAutomaticAssistanceCompleted(targetRoomId, newestMsg.id)) {
        state.lastAssistedId = newestMsg.id;
        state.pendingAssistantTriggerId = null;
        return;
    }
    if (state.failedAssistantId === newestMsg.id && Date.now() < (state.assistantRetryAt || 0)) return;

    if (state.responseAssistantLoading) {
        state.pendingAssistantTriggerId = newestMsg.id;
        return;
    }

    console.log("Auto-generating narration and next-message draft:", newestMsg.id);
    handleGenerateDigest({ autoPlay: true, triggerMessageId: newestMsg.id });
}

async function recoverPrewarmedNarration(targetRoomId, messages) {
    const settings = getSettings();
    if (!settings.autoNarrate || !settings.backgroundNarrationPrewarm) return;
    const state = getRoomState(targetRoomId);
    if (state.responseAssistantLoading) return;
    const realReplies = (messages || []).filter(isRealAgentResponse);
    const newestReply = realReplies[realReplies.length - 1];
    if (!newestReply || newestReply.id === state.lastAssistedId) return;

    try {
        const response = await fetch(
            `/api/response-assistant/cached?room_id=${encodeURIComponent(targetRoomId)}&trigger_message_id=${encodeURIComponent(newestReply.id)}`
        );
        if (!response.ok) return;
        const payload = await response.json();
        const digestData = payload && payload.result;
        if (!digestData || !digestData.digest) return;

        state.digestText = digestData.digest;
        state.lastAssistedId = digestData.trigger_message_id || newestReply.id;
        state.failedAssistantId = null;
        state.assistantRetryAt = 0;
        state.pendingAssistantTriggerId = null;
        applySuggestedDraft(targetRoomId, digestData.suggested_message);
        state.suggestionPhase = digestData.phase || "";
        state.suggestionRationale = digestData.rationale || "";
        applySmartQuickSuggestions(targetRoomId, digestData.quick_suggestions);

        const sourceIds = digestData.included_message_ids || [];
        const sourceMessages = messages.filter(message => sourceIds.includes(message.id));
        state.sourcesToggleText = `Show Sources (${sourceMessages.length})`;
        state.digestSourcesHtml = sourceMessages.map(message => {
            const author = message.name || message.username;
            let snippet = message.text || "";
            if (snippet.length > 85) snippet = snippet.substring(0, 85) + "...";
            return `
                <div class="source-item" data-source-id="${message.id}">
                    <span class="source-author">@${escapeHTML(author)}:</span>
                    <span class="source-snippet">${escapeHTML(snippet)}</span>
                </div>
            `;
        }).join("");
        state.digestSourcesVisible = sourceMessages.length > 0;

        if (targetRoomId === activeRoomId) {
            currentDigestText = state.digestText;
            digestContent.innerText = currentDigestText;
            updateNarrationPlayState();
            setAssistantStatus("Narration ready; press Play for audio", "ready");
            sourcesToggleText.innerText = state.sourcesToggleText;
            sourcesList.innerHTML = state.digestSourcesHtml;
            bindSourceItemHandlers();
            digestSourcesContainer.style.display = sourceMessages.length > 0 ? "block" : "none";
        }
    } catch (_error) {
        // Cache recovery is an acceleration only. The active-room automatic
        // path continues to work normally when nothing was pre-generated.
    }
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
            // The background job may still be running when the room opens.
            // Keep checking the completed-cache on later polls until it is
            // ready; this is read-only and never starts a second generation.
            void recoverPrewarmedNarration(targetRoomId, data.messages);
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
        transcriptFeed.dataset.renderSignature = "empty";
        updateJumpToLatestButton();
        return;
    }

    // History polls every few seconds. Rewriting the conversation DOM without
    // restoring the reading position is what made mid-message text jump toward
    // the end. Keep Ed anchored unless a room open or explicit follow applies.
    const renderSignature = transcriptRenderSignature(messages, state);
    const hasSavedScroll = Boolean(
        state && state.savedScrollTop !== undefined && state.savedScrollTop !== null
    );
    const isNearBottom = (transcriptFeed.scrollHeight - transcriptFeed.scrollTop - transcriptFeed.clientHeight) < 120;
    const isInitialLoad = (lastMessageTimestamp === null);

    let shouldScroll = false;
    if (messages.length > 0) {
        const latestMsg = messages[messages.length - 1];
        if (lastMessageTimestamp !== latestMsg.timestamp) {
            lastMessageTimestamp = latestMsg.timestamp;
            shouldScroll = true;
        }
    }

    const followNewMessages = Boolean(getSettings().autoScrollNewMessages && isNearBottom);
    const shouldJumpToEnd = shouldScroll && (isInitialLoad || followNewMessages);

    if (
        !hasSavedScroll
        && !shouldJumpToEnd
        && transcriptFeed.dataset.renderSignature === renderSignature
        && transcriptFeed.querySelector(".chat-msg, .system-event-chip")
    ) {
        // Nothing visible changed; leave the DOM and scroll position alone.
        updateJumpToLatestButton();
        return;
    }

    const readingAnchor = (!hasSavedScroll && !shouldJumpToEnd)
        ? captureTranscriptReadingAnchor()
        : null;

    // Generate HTML for Load Older button/loader at the top
    let loadOlderBtnHtml = '';
    if (state && state.hasMoreOlder) {
        if (state.loadingOlder) {
            loadOlderBtnHtml = `
                <div class="load-older-container" style="text-align: center; padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); margin-bottom: 16px;">
                    <span class="material-symbols-rounded spinning" style="font-size: calc(var(--system-font-size) * 1.1); vertical-align: middle; display: inline-block;">progress_activity</span>
                    <span style="font-size: calc(var(--system-font-size) * 0.85); opacity: 0.8; vertical-align: middle; margin-left: 4px;">Loading older messages...</span>
                </div>
            `;
        } else {
            loadOlderBtnHtml = `
                <div class="load-older-container" style="text-align: center; padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); margin-bottom: 16px;">
                    <button id="btn-load-older" class="btn btn-secondary btn-sm" onclick="handleLoadOlder()" style="padding: 6px 14px; font-size: calc(var(--system-font-size) * 0.8); height: auto;">
                        <span class="material-symbols-rounded" style="font-size: calc(var(--system-font-size) * 1.1); vertical-align: middle; margin-right: 4px;">history</span>Load Older Messages
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

        const escapedRawText = escapeHTML(msg.text || "");
        return `
            <div class="${cardClass}" data-id="${msg.id}">
                <div class="chat-header">
                    <span class="chat-author">${escapeHTML(displayName)}</span>
                    <div class="chat-header-meta">
                        ${respTimeBadge}
                        <span class="chat-time">${timeStr}</span>
                        <button class="btn-copy-msg" type="button" data-raw-text="${escapedRawText}" title="Copy message text" aria-label="Copy message text">
                            <span class="material-symbols-rounded">content_copy</span>
                        </button>
                    </div>
                </div>
                <div class="chat-body">${renderMarkdown(msg.text)}</div>
            </div>
        `;
    }).join("");

    transcriptFeed.innerHTML = loadOlderBtnHtml + msgsHtml;
    transcriptFeed.dataset.renderSignature = renderSignature;

    // Opening a room still restores its saved position (or starts at the newest
    // message); following future messages is an explicit, default-off preference.
    // Poll re-renders always restore the pre-render reading anchor.
    if (hasSavedScroll) {
        transcriptFeed.scrollTop = state.savedScrollTop;
        state.savedScrollTop = null;
    } else if (shouldJumpToEnd) {
        transcriptFeed.scrollTop = transcriptFeed.scrollHeight;
    } else if (readingAnchor) {
        restoreTranscriptReadingAnchor(readingAnchor);
    }
    updateJumpToLatestButton();
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
                    <span class="material-symbols-rounded spinning" style="font-size: calc(var(--system-font-size) * 0.8); display: inline-block;">sync</span>
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
        statsBar.innerHTML = `<span class="stats-label" style="font-weight: 600; opacity: 0.7; font-size: calc(var(--system-font-size) * 0.8); margin-right: 12px; display: inline-flex; align-items: center; gap: 4px;"><span class="material-symbols-rounded" style="font-size: calc(var(--system-font-size) * 0.95);">history</span> Recent Window Stats:</span>` + items;
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
    const browserSpeechAvailable = (
        'speechSynthesis' in window
        && typeof window.speechSynthesis.cancel === "function"
    );
    if (browserSpeechAvailable) {
        // Warm up the fallback engine.
        window.speechSynthesis.cancel();
    }

    voiceMode = getSettings().voiceMode;
    ttsProvider = "browser";
    chatterboxAvailable = false;
    ttsStatusChip.className = "status-chip active";
    ttsStatusChip.querySelector(".status-label").innerText = voiceMode === "nice"
        ? "Nice voice: Checking server"
        : "Fast voice: Ready";
    updateVoiceModeUI(voiceMode);
    // The model and its cache remain on the Gateway host. A phone only receives
    // transient WAV chunks; if this check or playback fails, use its browser voice.
    if (typeof fetch === "function") {
        fetch("/api/gateway/tts/status")
            .then(response => response.ok ? response.json() : null)
            .then(status => {
                chatterboxAvailable = Boolean(status && status.chatterbox_available);
                updateVoiceModeUI(voiceMode);
                if (!browserSpeechAvailable && !chatterboxAvailable) {
                    ttsStatusChip.querySelector(".status-label").innerText = "Speech Engine: Unsupported";
                }
            })
            .catch(() => {
                chatterboxAvailable = false;
                updateVoiceModeUI(voiceMode);
                if (!browserSpeechAvailable) {
                    ttsStatusChip.querySelector(".status-label").innerText = "Speech Engine: Unsupported";
                }
            });
    }
}

async function handleGenerateDigest(options = {}) {
    if (!activeRoomId) return;
    const targetRoomId = activeRoomId;
    const targetState = getRoomState(targetRoomId);
    const automaticTriggerId = options.triggerMessageId || null;
    let automaticClaimed = false;
    let automaticCoordinationAvailable = false;
    let automaticDeferredToOtherTab = false;
    if (targetState.responseAssistantLoading) {
        if (automaticTriggerId) {
            targetState.pendingAssistantTriggerId = automaticTriggerId;
        }
        return;
    }
    if (automaticTriggerId) {
        if (isAutomaticAssistanceCompleted(targetRoomId, automaticTriggerId)) {
            targetState.lastAssistedId = automaticTriggerId;
            targetState.pendingAssistantTriggerId = null;
            return;
        }
        const claim = tryClaimAutomaticAssistance(targetRoomId, automaticTriggerId);
        automaticClaimed = claim.claimed;
        automaticCoordinationAvailable = claim.coordinated;
        if (!automaticClaimed) {
            // Keep the response pending while another tab owns the short lease.
            // A later poll will observe its completion marker or retry after expiry.
            targetState.pendingAssistantTriggerId = automaticTriggerId;
            return;
        }
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

    let heartbeatTimer = null;
    if (automaticTriggerId && automaticClaimed) {
        heartbeatTimer = setInterval(() => {
            touchAutomaticAssistanceClaim(targetRoomId, automaticTriggerId);
        }, 15000);
    }

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
                trigger_message_id: automaticTriggerId,
                history_limit: limit
            })
        });

        const digestData = await digestResponse.json();
        if (!digestResponse.ok) throw new Error(digestData.detail || "Failed to generate response assistance");

        const automaticAlreadyCompleted = Boolean(
            automaticTriggerId
            && isAutomaticAssistanceCompleted(targetRoomId, automaticTriggerId)
        );
        const ownsCoordinatedClaim = Boolean(
            automaticTriggerId
            && automaticCoordinationAvailable
            && ownsAutomaticAssistanceClaim(targetRoomId, automaticTriggerId)
        );
        const automaticActionAllowed = !automaticTriggerId || (
            automaticCoordinationAvailable
                ? (ownsCoordinatedClaim && !automaticAlreadyCompleted)
                : true
        );

        if (!automaticActionAllowed) {
            // A completed marker proves another tab applied the result. Without
            // one, retain the trigger so a later poll can recover after the
            // current owner's bounded lease expires.
            if (automaticAlreadyCompleted) {
                targetState.lastAssistedId = digestData.trigger_message_id || automaticTriggerId;
                targetState.failedAssistantId = null;
                targetState.assistantRetryAt = 0;
                targetState.pendingAssistantTriggerId = null;
            } else {
                targetState.pendingAssistantTriggerId = automaticTriggerId;
                automaticDeferredToOtherTab = true;
            }
            if (targetRoomId === activeRoomId) {
                setAssistantStatus(
                    automaticAlreadyCompleted
                        ? "Automatic: handled in another tab"
                        : "Automatic: another tab is handling this reply",
                    "on"
                );
                digestContent.innerText = automaticAlreadyCompleted
                    ? "This response was already narrated and drafted by another console tab."
                    : "Another console tab currently owns this response. This tab will recover the cached result if that tab does not finish.";
            }
            return;
        }

        // A cached replay can safely restore the visible digest and draft after
        // an abandoned browser lease or in environments without storage.
        // It must not replay audio automatically.
        const recoveredCachedResult = Boolean(
            automaticTriggerId
            && digestData.automatic_action_allowed === false
        );

        targetState.digestText = digestData.digest;
        targetState.lastAssistedId = digestData.trigger_message_id || options.triggerMessageId || null;
        targetState.failedAssistantId = null;
        targetState.assistantRetryAt = 0;
        if (targetRoomId === activeRoomId) {
            setAssistantStatus(
                recoveredCachedResult
                    ? "Automatic: recovered draft ready; press Play for audio"
                    : (DISABLE_AUTO_NARRATION
                        ? "Automatic: draft ready; press Play for audio"
                        : "Automatic: draft ready"),
                "ready"
            );
        }

        const inserted = applySuggestedDraft(targetRoomId, digestData.suggested_message);
        targetState.suggestionPhase = digestData.phase || "";
        targetState.suggestionRationale = digestData.rationale || "";
        applySmartQuickSuggestions(targetRoomId, digestData.quick_suggestions);
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
            updateNarrationPlayState();
            sourcesToggleText.innerText = targetState.sourcesToggleText;
            sourcesList.innerHTML = targetState.digestSourcesHtml;
            bindSourceItemHandlers();
            if (sourceIds.length > 0) {
                digestSourcesContainer.style.display = "block";
            } else {
                digestSourcesContainer.style.display = "none";
            }
        }

        if (
            targetRoomId === activeRoomId
            && !DISABLE_AUTO_NARRATION
            && options.autoPlay !== false
            && !recoveredCachedResult
        ) {
            handleStop();
            speakText(currentDigestText);
        }
        if (automaticTriggerId) {
            markAutomaticAssistanceCompleted(targetRoomId, automaticTriggerId);
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
        if (heartbeatTimer) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
        if (automaticClaimed && automaticTriggerId) {
            releaseAutomaticAssistanceClaim(targetRoomId, automaticTriggerId);
        }
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
            && !automaticDeferredToOtherTab
        ) {
            targetState.pendingAssistantTriggerId = null;
            handleGenerateDigest({ autoPlay: true, triggerMessageId: pendingTrigger });
        } else if (pendingTrigger === targetState.lastAssistedId) {
            targetState.pendingAssistantTriggerId = null;
        }
    }
}

function setPlaybackUI(playing, label = null) {
    isPlaying = playing;
    visualizer.classList.toggle("playing", playing);
    playIcon.textContent = playing ? "pause" : "play_arrow";
    if (label) ttsStatusChip.querySelector(".status-label").innerText = label;
}

function splitNarrationIntoChunks(text) {
    const clean = String(text || "").trim();
    if (!clean) return [];
    const sentences = clean.match(/[^.!?]+[.!?]+(?:["'’)]*)|[^.!?]+$/g) || [clean];
    const chunks = [];
    for (const sentence of sentences) {
        const part = sentence.trim();
        if (!part) continue;
        if (part.length <= 1800) {
            chunks.push(part);
            continue;
        }
        let words = part.split(/\s+/);
        let current = "";
        for (const word of words) {
            const candidate = current ? `${current} ${word}` : word;
            if (candidate.length > 1800 && current) {
                chunks.push(current);
                current = word;
            } else {
                current = candidate;
            }
        }
        if (current) chunks.push(current);
    }
    return chunks;
}

function speakBrowserText(text) {
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
        setPlaybackUI(true, "Speech Engine: Browser speaking");
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Speaking`;
    };

    currentUtterance.onend = () => {
        setPlaybackUI(false, "Speech Engine: Browser ready");
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Ready`;
        currentUtterance = null;
    };

    currentUtterance.onerror = (e) => {
        console.error("SpeechSynthesis error:", e);
        setPlaybackUI(false, "Speech Engine: Browser error");
        ttsStatusChip.querySelector(".status-label").innerText = `Speech Engine: Error`;
        currentUtterance = null;
    };

    window.speechSynthesis.speak(currentUtterance);
}

// Kept as the stable local fallback seam used by the frontend harness and by
// browsers without an Audio implementation.
function speakText(text) {
    speakBrowserText(text);
}

async function speakChatterboxText(text) {
    if (typeof Audio === "undefined" || typeof URL === "undefined" || !URL.createObjectURL) {
        throw new Error("Remote audio playback is unavailable in this browser");
    }
    const chunks = splitNarrationIntoChunks(text);
    const token = {};
    remotePlayback = { token, chunks, index: 0, loading: true };
    setPlaybackUI(true, "Speech Engine: Chatterbox preparing");
    try {
        for (let index = 0; index < chunks.length; index += 1) {
            if (!remotePlayback || remotePlayback.token !== token) return;
            remotePlayback.index = index;
            const response = await fetch("/api/gateway/tts/speak", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text: chunks[index],
                    voice_mode: "nice",
                    provider: "chatterbox",
                    mode: "summary",
                    rate: Math.round(200 * speechRate)
                })
            });
            if (!response.ok) throw new Error(`Chatterbox returned ${response.status}`);
            const blob = await response.blob();
            if (!remotePlayback || remotePlayback.token !== token) return;
            const audio = new Audio(URL.createObjectURL(blob));
            currentRemoteAudio = audio;
            remotePlayback.loading = false;
            setPlaybackUI(true, "Speech Engine: Chatterbox speaking");
            await new Promise((resolve, reject) => {
                audio.onended = resolve;
                audio.onerror = () => reject(new Error("Remote audio playback failed"));
                Promise.resolve(audio.play()).catch(reject);
            });
            URL.revokeObjectURL(audio.src);
            currentRemoteAudio = null;
        }
    } finally {
        if (remotePlayback && remotePlayback.token === token) {
            remotePlayback = null;
            currentRemoteAudio = null;
            setPlaybackUI(false, "Speech Engine: Chatterbox ready");
        }
    }
}

function startNarration(text) {
    if (voiceMode === "nice" && chatterboxAvailable) {
        ttsProvider = "chatterbox";
        speakChatterboxText(text).catch(error => {
            console.warn("Chatterbox playback failed; using browser speech:", error);
            if (remotePlayback) remotePlayback = null;
            if (currentRemoteAudio) {
                try { currentRemoteAudio.pause(); } catch (e) {}
                currentRemoteAudio = null;
            }
            ttsProvider = "browser";
            chatterboxAvailable = false;
            updateVoiceModeUI(voiceMode);
            speakBrowserText(text);
        });
        return;
    }
    ttsProvider = "browser";
    speakBrowserText(text);
}

function handlePlayPause() {
    // Generation is intentionally separate: an empty Play control is inert.
    // Generate Digest creates both the narration and suggested reply.
    if (!String(currentDigestText || "").trim()) return;

    if (currentRemoteAudio || (remotePlayback && remotePlayback.loading)) {
        if (currentRemoteAudio) {
            if (currentRemoteAudio.paused) {
                currentRemoteAudio.play().catch(() => {});
                setPlaybackUI(true, "Speech Engine: Chatterbox speaking");
            } else {
                currentRemoteAudio.pause();
                setPlaybackUI(false, "Speech Engine: Chatterbox paused");
            }
        }
    } else if (window.speechSynthesis.speaking) {
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
        startNarration(currentDigestText);
    }
}

function handleStop() {
    if (remotePlayback) {
        remotePlayback = null;
        if (currentRemoteAudio) {
            try { currentRemoteAudio.pause(); } catch (e) {}
            if (typeof URL !== "undefined" && URL.revokeObjectURL && currentRemoteAudio.src) {
                URL.revokeObjectURL(currentRemoteAudio.src);
            }
            currentRemoteAudio = null;
        }
        fetch("/api/gateway/tts/stop", { method: "POST" }).catch(() => {});
    }
    window.speechSynthesis.cancel();
    currentUtterance = null;
    updateVoiceModeUI(voiceMode);
}

function handleSpeedChange() {
    speechRate = parseFloat(speedRange.value);
    speedVal.innerText = `${speechRate.toFixed(1)}x`;

    // If speaking, restart from the beginning (or let the rate change take effect for the next utterance)
    if (currentRemoteAudio || remotePlayback) {
        handleStop();
        startNarration(currentDigestText);
    } else if (window.speechSynthesis.speaking && currentUtterance) {
        // For browsers that support changing rate mid-speech
        currentUtterance.rate = speechRate;
        // In some browsers, we must cancel and restart to apply rate changes:
        if (isPlaying) {
            speakBrowserText(currentDigestText);
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
        syncCommandDraftState(commandInput.value);
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
        try {
            recognition.start();
        } catch (err) {
            console.error("Failed to start speech recognition:", err);
            stopListening();
        }
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

function extractRequestedAgent(text) {
    const match = text.match(/^@([a-zA-Z0-9_]+)/);
    if (match) return match[1];
    const settings = getSettings();
    return settings.defaultAgent || "codex";
}

async function sendMessage() {
    let text = commandInput.value.trim();
    if (!text || !activeRoomId) return;

    const targetRoomId = activeRoomId;
    // Auto-prepend the configured agent when the message has no explicit target.
    if (!text.startsWith("@")) {
        const settings = getSettings();
        const defAgent = settings.defaultAgent || "codex";
        text = `@${defAgent} ${text}`;
        commandInput.value = text;
        syncCommandDraftState(text);
    }

    if (btnSend) btnSend.disabled = true;
    showSendFeedback("Sending message...", "success");

    try {
        const prepareResponse = await fetch("/api/gateway/interact", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                schema_version: "1.0",
                client_id: "browser_console",
                input_mode: "text",
                raw_input: text,
                requested_room_id: targetRoomId,
                requested_agent: extractRequestedAgent(text)
            })
        });
        const prepareData = await prepareResponse.json();
        if (!prepareResponse.ok) {
            throw new Error(prepareData.detail || "Gateway could not prepare the message");
        }

        const snapshot = prepareData.confirmation_snapshot
            || prepareData.provider_metadata?.confirmation_snapshot;
        if (!snapshot) throw new Error("Gateway did not return a message dispatch snapshot");

        // Dispatch the prepared snapshot immediately; the user no longer needs
        // to interact with a second confirmation control in the composer.
        const response = await fetch("/api/gateway/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(snapshot)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Failed to post message");
        }

        if (data.status === "posted") {
            const state = getRoomState(targetRoomId);
            // Do not erase text Ed started typing after Send was pressed.
            const inputStillMatches = activeRoomId === targetRoomId
                && commandInput.value.trim() === text;
            if (inputStillMatches) {
                localStorage.removeItem("vc_draft_" + targetRoomId);
                state.draftText = "";
                commandInput.value = "";
                syncCommandDraftState("");
            } else if (activeRoomId !== targetRoomId) {
                localStorage.removeItem("vc_draft_" + targetRoomId);
                state.draftText = "";
            }

            // Display successful send feedback with message ID
            const msgId = data.rocket_chat_msg_ids?.[0] || "unknown";
            showSendFeedback(`Message sent successfully. ID: ${msgId}`, "success");

            loadHistory(); // Reload history immediately to see the new message
        } else {
            showSendFeedback("Error sending message to Rocket.Chat", "error");
        }
    } catch (err) {
        console.error("Post message failed:", err);
        showSendFeedback("Failed to send message: " + err.message, "error");
    } finally {
        if (btnSend) btnSend.disabled = false;
        applyPendingAttentionQueueIfReady();
    }
}
