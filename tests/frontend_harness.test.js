const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const historyState = require("../frontend/history_state.js");

function classList() {
    const values = new Set();
    return {
        add: (...items) => items.forEach(item => values.add(item)),
        remove: (...items) => items.forEach(item => values.delete(item)),
        contains: item => values.has(item),
        toggle: (item, force) => {
            if (force === true) {
                values.add(item);
                return true;
            }
            if (force === false) {
                values.delete(item);
                return false;
            }
            if (values.has(item)) {
                values.delete(item);
                return false;
            }
            values.add(item);
            return true;
        },
    };
}

function element() {
    const listeners = {};
    const attributes = {};
    const styleValues = { display: "none" };
    styleValues.setProperty = (name, value) => { styleValues[name] = value; };
    styleValues.getPropertyValue = name => styleValues[name] || "";
    styleValues.removeProperty = name => { delete styleValues[name]; };
    return {
        classList: classList(),
        dataset: {},
        disabled: false,
        innerHTML: "",
        innerText: "",
        offsetWidth: 1,
        readOnly: false,
        scrollTop: 0,
        style: styleValues,
        value: "",
        listeners,
        addEventListener: (name, handler) => { listeners[name] = handler; },
        focus: () => {},
        getAttribute: name => attributes[name] ?? null,
        querySelector: () => element(),
        querySelectorAll: () => [],
        scrollIntoView: () => {},
        setAttribute: (name, value) => { attributes[name] = String(value); },
    };
}

function loadFrontend(options = {}) {
    const elements = new Map();
    const body = element();
    const documentListeners = {};
    const document = {
        body,
        readyState: "loading",
        addEventListener: (name, handler) => { documentListeners[name] = handler; },
        createElement: () => element(),
        getElementById: id => {
            if (!elements.has(id)) elements.set(id, element());
            return elements.get(id);
        },
        querySelector: selector => element(),
        querySelectorAll: () => [],
    };
    const storage = options.storage || {};
    const storageWrites = [];
    const spoken = [];
    const intervals = [];
    const sandbox = {
        console,
        document,
        fetch: url => Promise.resolve({
            ok: true,
            json: async () => url === "/api/gateway/interact"
                ? {
                    status: "awaiting_confirmation",
                    confirmation_snapshot: {
                        room_id: "room-1",
                        exact_message: "Test confirmation message",
                        nonce: "nonce-default"
                    }
                }
                : ({ success: true, messages: [] })
        }),
        localStorage: {
            getItem: key => storage[key] || null,
            setItem: (key, val) => {
                storage[key] = val;
                storageWrites.push({ key, val });
            },
            removeItem: key => { delete storage[key]; }
        },
        setInterval: (callback, delay) => {
            intervals.push({ callback, delay });
            return intervals.length;
        },
        clearInterval: () => {},
        setTimeout: callback => { callback(); return 0; },
        window: {
            VoiceChannelHistoryState: historyState,
            confirm: options.confirm || (() => true),
            speechSynthesis: {
                cancel: () => {},
                getVoices: () => [],
                speak: utterance => { spoken.push(utterance.text); },
                speaking: false
            },
        },
    };
    if (options.enableSpeech) {
        sandbox.SpeechSynthesisUtterance = function SpeechSynthesisUtterance(text) {
            this.text = text;
        };
    }
    vm.createContext(sandbox);
    const source = fs.readFileSync(path.join(__dirname, "..", "frontend", "index.js"), "utf8");
    vm.runInContext(source, sandbox);
    return { elements, sandbox, spoken, storage, storageWrites, documentListeners, intervals };
}

test("the channel rail is never refreshed on a timer", () => {
    const app = loadFrontend();
    app.documentListeners.DOMContentLoaded();

    // Status ~5s and active conversation history ~4s only. The rail/attention 7s
    // timer is deliberately gone: the list must not move on its own cadence.
    assert.deepEqual(
        app.intervals.map(interval => interval.delay),
        [5000, 4000]
    );
});

test("the channels refresh button re-pulls the room list on demand", async () => {
    const app = loadFrontend();
    const roomCalls = [];
    app.sandbox.fetch = url => {
        roomCalls.push(url);
        return Promise.resolve({ ok: true, json: async () => ({ success: true, rooms: [] }) });
    };

    const button = app.sandbox.document.getElementById("btn-refresh-channels");
    assert.equal(typeof button.listeners.click, "function");

    await button.listeners.click();

    assert.ok(roomCalls.some(url => url.startsWith("/api/rooms")));
    assert.equal(button.disabled, false);
    assert.equal(button.classList.contains("is-refreshing"), false);
});

test("a refresh already in flight is not started twice", async () => {
    const app = loadFrontend();
    let roomFetches = 0;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    app.sandbox.fetch = url => {
        if (url.startsWith("/api/rooms")) {
            roomFetches += 1;
            return gate.then(() => ({ ok: true, json: async () => ({ success: true, rooms: [] }) }));
        }
        return Promise.resolve({ ok: true, json: async () => ({ success: true, messages: [] }) });
    };

    const button = app.sandbox.document.getElementById("btn-refresh-channels");
    const first = button.listeners.click();
    await button.listeners.click();
    release();
    await first;

    assert.equal(roomFetches, 1);
});

test("webhook status chip makes live and fallback delivery visible", () => {
    const app = loadFrontend();
    const chip = app.sandbox.document.getElementById("webhook-status-chip");

    app.sandbox.updateWebhookStatusChip({ state: "healthy" });
    assert.equal(chip.dataset.state, "healthy");
    assert.equal(chip.className, "webhook-status-chip is-healthy");
    assert.match(chip.getAttribute("title"), /delivering verified message events/);

    app.sandbox.updateWebhookStatusChip({ state: "degraded_polling" });
    assert.equal(chip.dataset.state, "degraded");
    assert.equal(chip.className, "webhook-status-chip is-degraded");
    assert.match(chip.getAttribute("title"), /polling fallback/);
});

test("prepared narration uses the defined guarded play entry point", () => {
    const app = loadFrontend({ enableSpeech: true });
    vm.runInContext(`
        currentDigestText = "Prepared narration is ready.";
        digestContent.innerText = currentDigestText;
    `, app.sandbox);

    assert.equal(app.sandbox.playPreparedNarrationIfIdle(), true);
    assert.deepEqual(app.spoken, ["Prepared narration is ready."]);
    app.sandbox.window.speechSynthesis.speaking = true;
    assert.equal(app.sandbox.playPreparedNarrationIfIdle(), false);
    assert.equal(app.spoken.length, 1);
});

test("automatic assistance prepares the digest and draft without starting narration", async () => {
    const app = loadFrontend({ enableSpeech: true });
    vm.runInContext(`
        activeRoomId = "room-manual-audio";
        roomsList = [{ id: "room-manual-audio", name: "voice_channel" }];
    `, app.sandbox);
    app.sandbox.fetch = url => {
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [{
                        id: "agent-manual-audio",
                        lane: "agent",
                        text: "Finished",
                        event: { kind: "agent_response", agent: "codex" }
                    }]
                })
            });
        }
        if (url === "/api/response-assistant") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    digest: "The requested work is complete.",
                    suggested_message: "@grok Review the completed work.",
                    trigger_message_id: "agent-manual-audio",
                    automatic_action_allowed: true
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.handleGenerateDigest({
        autoPlay: true,
        triggerMessageId: "agent-manual-audio"
    });

    assert.equal(app.spoken.length, 0);
    assert.equal(
        app.sandbox.document.getElementById("command-input").value,
        "@grok Review the completed work."
    );
    app.sandbox.handlePlayPause();
    assert.deepEqual(app.spoken, ["The requested work is complete."]);
});

test("opening a room restores a prepared narration without a second generation", async () => {
    const app = loadFrontend();
    const requests = [];
    vm.runInContext(`
        activeRoomId = "room-prewarmed";
        roomsList = [{ id: "room-prewarmed", name: "voice_channel" }];
    `, app.sandbox);
    app.sandbox.fetch = (url, options = {}) => {
        requests.push({ url, options });
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [{
                        id: "prepared-agent-reply",
                        lane: "agent",
                        name: "Codex",
                        text: "The work is ready.",
                        event: { kind: "agent_response", agent: "codex" }
                    }]
                })
            });
        }
        if (url.startsWith("/api/response-assistant/cached")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    result: {
                        digest: "Codex completed the requested work and it is ready to review.",
                        suggested_message: "@grok Review the completed work.",
                        trigger_message_id: "prepared-agent-reply",
                        included_message_ids: ["prepared-agent-reply"]
                    }
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.recoverPrewarmedNarration("room-prewarmed", [{
        id: "prepared-agent-reply",
        lane: "agent",
        name: "Codex",
        text: "The work is ready.",
        event: { kind: "agent_response", agent: "codex" }
    }]);

    assert.equal(
        app.sandbox.document.getElementById("digest-content").innerText,
        "Codex completed the requested work and it is ready to review."
    );
    assert.equal(
        app.sandbox.document.getElementById("command-input").value,
        "@grok Review the completed work."
    );
    assert.equal(requests.some(item => item.url === "/api/response-assistant"), false);
    assert.equal(
        requests.some(item => item.url.startsWith("/api/response-assistant/cached")),
        true
    );
});

test("prepared narration recovery retries the completed cache without generating", async () => {
    const app = loadFrontend();
    const requests = [];
    let prepared = false;
    vm.runInContext(`
        activeRoomId = "room-prewarm-inflight";
        roomsList = [{ id: "room-prewarm-inflight", name: "voice_channel" }];
    `, app.sandbox);
    app.sandbox.fetch = (url, options = {}) => {
        requests.push({ url, options });
        if (url.startsWith("/api/response-assistant/cached")) {
            if (!prepared) {
                return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
            }
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    result: {
                        digest: "The background narration has finished preparing.",
                        trigger_message_id: "agent-prewarm-inflight",
                        included_message_ids: ["agent-prewarm-inflight"]
                    }
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };
    const messages = [{
        id: "agent-prewarm-inflight",
        lane: "agent",
        name: "Codex",
        text: "The work is ready.",
        event: { kind: "agent_response", agent: "codex" }
    }];

    await app.sandbox.recoverPrewarmedNarration("room-prewarm-inflight", messages);
    assert.equal(app.sandbox.document.getElementById("digest-content").innerText, "");
    prepared = true;
    await app.sandbox.recoverPrewarmedNarration("room-prewarm-inflight", messages);

    assert.equal(
        app.sandbox.document.getElementById("digest-content").innerText,
        "The background narration has finished preparing."
    );
    assert.equal(requests.filter(item => item.url.startsWith("/api/response-assistant/cached")).length, 2);
    assert.equal(requests.some(item => item.url === "/api/response-assistant"), false);
});

test("editable narration is saved per room and Play reads the edited words", async () => {
    const app = loadFrontend({ enableSpeech: true });
    vm.runInContext(`
        activeRoomId = "room-edit-narration";
        currentDigestText = "Original summary.";
        digestContent.innerText = currentDigestText;
        updateNarrationPlayState();
    `, app.sandbox);

    const editor = app.sandbox.document.getElementById("digest-content");
    const play = app.sandbox.document.getElementById("btn-play-pause");
    editor.innerText = "Edited summary for careful review.";
    app.sandbox.handleNarrationEdit();

    assert.equal(play.disabled, false);
    assert.equal(
        vm.runInContext('getRoomState("room-edit-narration").digestText', app.sandbox),
        "Edited summary for careful review."
    );
    app.sandbox.handlePlayPause();
    assert.deepEqual(app.spoken, ["Edited summary for careful review."]);
});

test("Play is compact and inert until Generate Digest creates narration and a suggestion", async () => {
    const app = loadFrontend({ enableSpeech: true });
    let responseAssistantRequests = 0;
    vm.runInContext(`
        activeRoomId = "room-empty-narration";
        roomsList = [{ id: "room-empty-narration", name: "voice_channel" }];
        currentDigestText = "";
        updateNarrationPlayState();
    `, app.sandbox);
    app.sandbox.fetch = url => {
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [{ id: "message-1", lane: "agent", text: "Work complete" }]
                })
            });
        }
        if (url === "/api/response-assistant") {
            responseAssistantRequests += 1;
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    digest: "Generated narration.",
                    suggested_message: "@grok Please review the work.",
                    included_message_ids: ["message-1"]
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    const play = app.sandbox.document.getElementById("btn-play-pause");
    assert.equal(play.disabled, true);
    app.sandbox.handlePlayPause();
    await Promise.resolve();
    assert.equal(responseAssistantRequests, 0);
    assert.equal(app.spoken.length, 0);

    await app.sandbox.handleGenerateDigest();
    assert.equal(responseAssistantRequests, 1);
    assert.equal(play.disabled, false);
    assert.equal(
        app.sandbox.document.getElementById("digest-content").innerText,
        "Generated narration."
    );
    assert.equal(
        app.sandbox.document.getElementById("command-input").value,
        "@grok Please review the work."
    );
    assert.equal(app.spoken.length, 0);
});

test("composer divider clamps, persists, and restores both pane boundaries", () => {
    const app = loadFrontend();
    vm.runInContext(`
        transcriptFeedContainer.offsetHeight = 440;
        composerContainer.offsetHeight = 240;
        initComposerResizer();
    `, app.sandbox);
    const composer = app.sandbox.document.getElementById("composer-container");
    const handle = app.sandbox.document.getElementById("composer-resize-handle");

    assert.equal(typeof handle.listeners.pointerdown, "function");
    assert.equal(typeof handle.listeners.keydown, "function");
    assert.equal(app.sandbox.setComposerHeight(999, true), 510);
    assert.equal(composer.style.height, "510px");
    assert.equal(app.storage.vc_composer_height_px, "510");
    assert.equal(handle.getAttribute("aria-valuenow"), "510");
    assert.equal(app.sandbox.setComposerHeight(1, true), 250);
    assert.equal(composer.style.height, "250px");

    handle.listeners.pointerdown({
        button: 0,
        pointerId: 7,
        clientY: 500,
        preventDefault: () => {}
    });
    app.documentListeners.pointermove({ pointerId: 7, clientY: 400, preventDefault: () => {} });
    app.documentListeners.pointerup({ pointerId: 7 });
    assert.equal(composer.style.height, "350px");
    assert.equal(app.storage.vc_composer_height_px, "350");

    handle.listeners.keydown({ key: "ArrowUp", preventDefault: () => {} });
    assert.equal(composer.style.height, "374px");
    assert.equal(app.storage.vc_composer_height_px, "374");

    const restored = loadFrontend({ storage: { vc_composer_height_px: "300" } });
    vm.runInContext(`
        transcriptFeedContainer.offsetHeight = 440;
        composerContainer.offsetHeight = 240;
        initComposerResizer();
    `, restored.sandbox);
    assert.equal(
        restored.sandbox.document.getElementById("composer-container").style.height,
        "300px"
    );
});

test("conversation message headers are the native sticky sender labels", () => {
    const app = loadFrontend();
    const feed = app.sandbox.document.getElementById("transcript-feed");
    app.sandbox.renderTranscript([{
        id: "sticky-agent-message",
        lane: "agent",
        name: "AGY",
        text: "This is a long message that remains readable while its sender label stays at the top of the scroll viewport.",
        timestamp: 2000
    }]);

    assert.match(feed.innerHTML, /class="chat-msg agent"/);
    assert.match(feed.innerHTML, /class="chat-header"/);
    assert.match(feed.innerHTML, /class="chat-author">AGY<\/span>/);

    const css = fs.readFileSync(path.join(__dirname, "..", "frontend", "index.css"), "utf8");
    assert.match(css, /\.chat-header\s*\{[\s\S]*?position:\s*sticky/);
    assert.match(css, /\.chat-header\s*\{[\s\S]*?top:\s*0/);
    assert.doesNotMatch(css, /transcript-current-sender/);
    assert.doesNotMatch(fs.readFileSync(path.join(__dirname, "..", "frontend", "index.js"), "utf8"), /CurrentSenderIndicator/);
});

test("jump-to-latest control scrolls to the beginning of the final conversation message", () => {
    const app = loadFrontend();
    const feed = app.sandbox.document.getElementById("transcript-feed");
    const button = app.sandbox.document.getElementById("btn-jump-to-latest");
    const latestMessage = { offsetTop: 640 };
    feed.scrollTop = 120;
    feed.querySelectorAll = selector => selector === ".chat-msg" ? [latestMessage] : [];
    feed.scrollTo = options => { feed.lastScrollTo = options; };

    app.sandbox.bindJumpToLatestMessageControl();
    app.sandbox.updateJumpToLatestButton();
    assert.equal(button.hidden, false);

    button.listeners.click();
    assert.equal(feed.lastScrollTo.top, 640);
    assert.equal(feed.lastScrollTo.behavior, "smooth");

    feed.scrollTop = 640;
    app.sandbox.updateJumpToLatestButton();
    assert.equal(button.hidden, true);

    const html = fs.readFileSync(path.join(__dirname, "..", "frontend", "index.html"), "utf8");
    assert.match(html, /id="btn-jump-to-latest"/);
    assert.match(html, /Jump to the beginning of the latest message/);
});

test("narrator divider clamps, persists, and keeps the conversation pane visible", () => {
    const app = loadFrontend();
    vm.runInContext(`
        window.innerWidth = 1400;
        channelsSidebar.offsetWidth = 280;
        narratorSidebar.offsetWidth = 340;
        narratorResizeHandle.offsetWidth = 12;
        initNarratorResizer();
    `, app.sandbox);
    const sidebar = app.sandbox.document.getElementById("narrator-sidebar");
    const handle = app.sandbox.document.getElementById("narrator-resize-handle");

    assert.equal(typeof handle.listeners.pointerdown, "function");
    assert.equal(typeof handle.listeners.keydown, "function");
    // 1,400px viewport − 280px channels − 12px grip − 380px conversation.
    assert.equal(app.sandbox.setNarratorWidth(999, true), 728);
    assert.equal(sidebar.style.getPropertyValue("--narrator-sidebar-width"), "728px");
    assert.equal(app.storage.vc_narrator_width_px, "728");
    assert.equal(handle.getAttribute("aria-valuemax"), "728");
    assert.equal(app.sandbox.setNarratorWidth(1, true), 340);

    handle.listeners.pointerdown({
        button: 0,
        pointerId: 8,
        clientX: 1000,
        preventDefault: () => {}
    });
    // Moving the left edge left expands the right-aligned narrator panel.
    app.documentListeners.pointermove({ pointerId: 8, clientX: 800, preventDefault: () => {} });
    app.documentListeners.pointerup({ pointerId: 8 });
    assert.equal(sidebar.style.getPropertyValue("--narrator-sidebar-width"), "540px");
    assert.equal(app.storage.vc_narrator_width_px, "540");

    handle.listeners.keydown({ key: "ArrowRight", preventDefault: () => {} });
    assert.equal(sidebar.style.getPropertyValue("--narrator-sidebar-width"), "516px");
    handle.listeners.keydown({ key: "End", preventDefault: () => {} });
    assert.equal(sidebar.style.getPropertyValue("--narrator-sidebar-width"), "728px");

    const restored = loadFrontend({ storage: { vc_narrator_width_px: "520" } });
    vm.runInContext(`
        window.innerWidth = 1400;
        channelsSidebar.offsetWidth = 280;
        narratorSidebar.offsetWidth = 340;
        narratorResizeHandle.offsetWidth = 12;
        initNarratorResizer();
    `, restored.sandbox);
    assert.equal(
        restored.sandbox.document.getElementById("narrator-sidebar").style
            .getPropertyValue("--narrator-sidebar-width"),
        "520px"
    );
});

test("channel sidebar divider clamps, persists, and restores a bounded width", () => {
    const app = loadFrontend();
    vm.runInContext(`
        channelsSidebar.offsetWidth = 300;
        initChannelsSidebar();
    `, app.sandbox);
    const sidebar = app.sandbox.document.getElementById("channels-sidebar");
    const handle = app.sandbox.document.getElementById("channels-resize-handle");

    assert.equal(typeof handle.listeners.pointerdown, "function");
    assert.equal(typeof handle.listeners.keydown, "function");
    assert.equal(app.sandbox.setChannelsWidth(999, true), 420);
    assert.equal(sidebar.style.getPropertyValue("--channels-sidebar-width"), "420px");
    assert.equal(app.storage.vc_channels_width_px, "420");
    assert.equal(app.sandbox.setChannelsWidth(1, true), 260);

    handle.listeners.pointerdown({
        button: 0,
        pointerId: 9,
        clientX: 260,
        preventDefault: () => {}
    });
    app.documentListeners.pointermove({ pointerId: 9, clientX: 340, preventDefault: () => {} });
    app.documentListeners.pointerup({ pointerId: 9 });
    assert.equal(sidebar.style.getPropertyValue("--channels-sidebar-width"), "340px");
    assert.equal(app.storage.vc_channels_width_px, "340");

    handle.listeners.keydown({ key: "End", preventDefault: () => {} });
    assert.equal(sidebar.style.getPropertyValue("--channels-sidebar-width"), "420px");

    const restored = loadFrontend({ storage: { vc_channels_width_px: "375" } });
    vm.runInContext("initChannelsSidebar();", restored.sandbox);
    assert.equal(
        restored.sandbox.document.getElementById("channels-sidebar").style
            .getPropertyValue("--channels-sidebar-width"),
        "375px"
    );
});

test("channel sidebar bottom control collapses and header control reopens it", () => {
    const app = loadFrontend();
    vm.runInContext("initChannelsSidebar();", app.sandbox);
    const sidebar = app.sandbox.document.getElementById("channels-sidebar");

    app.sandbox.document.getElementById("btn-collapse-channels").listeners.click();
    assert.equal(sidebar.classList.contains("collapsed"), true);
    assert.equal(app.storage.vc_channels_open, "false");

    app.sandbox.document.getElementById("btn-toggle-sidebar").listeners.click();
    assert.equal(sidebar.classList.contains("collapsed"), false);
    assert.equal(app.storage.vc_channels_open, "true");
    assert.equal(app.sandbox.document.getElementById("btn-toggle-sidebar").getAttribute("aria-expanded"), "true");
});

test("stale room history response is ignored when user switches rooms mid-flight", async () => {
    const app = loadFrontend();
    let resolveStale;
    const stalePromise = new Promise(resolve => { resolveStale = resolve; });

    // Mock fetch to delay for room-a
    app.sandbox.fetch = (url) => {
        if (url.includes("room-a")) {
            return stalePromise;
        }
        return Promise.resolve({
            ok: true,
            json: async () => ({
                success: true,
                messages: [{ id: "msg-b1", text: "Room B Message", lane: "user", timestamp: 2000 }]
            })
        });
    };

    // User is in room-a and triggers loadHistory
    vm.runInContext('activeRoomId = "room-a";', app.sandbox);
    const loadPromiseA = app.sandbox.loadHistory();

    // User switches to room-b before room-a completes
    vm.runInContext('selectRoom("room-b");', app.sandbox);

    // Resolve stale room-a fetch
    resolveStale({
        ok: true,
        json: async () => ({
            success: true,
            messages: [{ id: "msg-a1", text: "Stale Room A Message", lane: "user", timestamp: 1000 }]
        })
    });
    await loadPromiseA;

    // Room B state must NOT contain Stale Room A Message
    const stateB = vm.runInContext('getRoomState("room-b")', app.sandbox);
    assert.equal(stateB.orderedIds.includes("msg-a1"), false);
    assert.equal(stateB.orderedIds.includes("msg-b1"), true);
});

test("narrator sidebar toggle opens, closes, and persists state in localStorage", () => {
    const app = loadFrontend();
    const sidebar = app.sandbox.document.getElementById("narrator-sidebar");

    // Initially open (not collapsed)
    assert.equal(sidebar.classList.contains("collapsed"), false);

    // Close / collapse
    vm.runInContext('closeNarratorSidebar();', app.sandbox);
    assert.equal(sidebar.classList.contains("collapsed"), true);
    assert.equal(app.storage["narratorOpen"], "false");

    // Toggle open
    vm.runInContext('toggleNarratorSidebar();', app.sandbox);
    assert.equal(sidebar.classList.contains("collapsed"), false);
    assert.equal(app.storage["narratorOpen"], "true");

    // Init state from localStorage
    app.storage["narratorOpen"] = "true";
    vm.runInContext('initNarratorSidebarState();', app.sandbox);
    assert.equal(sidebar.classList.contains("collapsed"), false);
});

test("nice voice switch is opt-in and persists the selected voice mode", () => {
    const app = loadFrontend();
    app.sandbox.initVoiceModeControl();
    const toggle = app.sandbox.document.getElementById("toggle-nice-voice");

    assert.equal(toggle.checked, false);
    assert.equal(vm.runInContext('getSettings().voiceMode', app.sandbox), "fast");

    toggle.checked = true;
    toggle.listeners.change();
    assert.equal(vm.runInContext('getSettings().voiceMode', app.sandbox), "nice");
    assert.equal(JSON.parse(app.storage.vc_settings).voiceMode, "nice");

    toggle.checked = false;
    toggle.listeners.change();
    assert.equal(vm.runInContext('getSettings().voiceMode', app.sandbox), "fast");
    assert.equal(JSON.parse(app.storage.vc_settings).voiceMode, "fast");
});

test("automatic follow of new conversation messages is off by default and persists when enabled", () => {
    const app = loadFrontend();
    app.sandbox.initSettingsModal();
    const open = app.sandbox.document.getElementById("btn-open-settings");
    const save = app.sandbox.document.getElementById("btn-save-settings");
    const autoScroll = app.sandbox.document.getElementById("setting-auto-scroll-new-messages");

    assert.equal(vm.runInContext("getSettings().autoScrollNewMessages", app.sandbox), false);
    open.listeners.click();
    assert.equal(autoScroll.checked, false);

    autoScroll.checked = true;
    save.listeners.click();
    assert.equal(JSON.parse(app.storage.vc_settings).autoScrollNewMessages, true);
    assert.equal(vm.runInContext("getSettings().autoScrollNewMessages", app.sandbox), true);
});

test("background narration prewarm defaults to top attention and saves its gateway setting", async () => {
    const app = loadFrontend();
    const requests = [];
    app.sandbox.fetch = (url, options = {}) => {
        requests.push({ url, options });
        return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
    };
    app.sandbox.initSettingsModal();
    const open = app.sandbox.document.getElementById("btn-open-settings");
    const save = app.sandbox.document.getElementById("btn-save-settings");
    const prewarm = app.sandbox.document.getElementById("setting-background-narration-prewarm");
    const scope = app.sandbox.document.getElementById("setting-background-narration-prewarm-scope");

    open.listeners.click();
    assert.equal(prewarm.checked, true);
    assert.equal(scope.value, "top_attention");

    prewarm.checked = false;
    scope.value = "supervised";
    save.listeners.click();
    await Promise.resolve();

    const request = requests.find(item => item.url === "/api/narration/prewarm/config");
    assert.ok(request);
    assert.equal(request.options.method, "PUT");
    const body = JSON.parse(request.options.body);
    assert.equal(body.enabled, false);
    assert.equal(body.scope, "supervised");
    assert.equal(JSON.parse(app.storage.vc_settings).backgroundNarrationPrewarm, false);
});

test("new messages do not move a reader unless automatic follow is enabled", () => {
    const app = loadFrontend();
    const feed = app.sandbox.document.getElementById("transcript-feed");
    feed.clientHeight = 300;
    feed.scrollHeight = 1000;
    feed.scrollTop = 650;
    const first = [{ id: "one", lane: "agent", name: "Codex", text: "First", timestamp: 1 }];
    const second = [...first, { id: "two", lane: "agent", name: "Codex", text: "Second", timestamp: 2 }];

    app.sandbox.renderTranscript(first);
    feed.scrollTop = 650;
    app.sandbox.renderTranscript(second);
    assert.equal(feed.scrollTop, 650);

    vm.runInContext("saveSettings({ ...getSettings(), autoScrollNewMessages: true })", app.sandbox);
    feed.scrollHeight = 1200;
    feed.scrollTop = 850;
    app.sandbox.renderTranscript([...second, { id: "three", lane: "agent", name: "Codex", text: "Third", timestamp: 3 }]);
    assert.equal(feed.scrollTop, 1200);
});

test("history poll re-renders keep the reading position and never auto-jump to latest", () => {
    const app = loadFrontend();
    const feed = app.sandbox.document.getElementById("transcript-feed");
    const button = app.sandbox.document.getElementById("btn-jump-to-latest");
    feed.clientHeight = 300;
    feed.scrollHeight = 2000;
    const messages = [
        { id: "m1", lane: "agent", name: "Grok", text: "Paragraph one of a long answer.", timestamp: 10 },
        { id: "m2", lane: "agent", name: "Grok", text: "Paragraph two that the reader is still on.", timestamp: 20 },
        { id: "m3", lane: "agent", name: "Codex", text: "Latest message below the viewport.", timestamp: 30 },
    ];

    app.sandbox.renderTranscript(messages);
    feed.scrollTop = 420;
    const htmlAfterFirst = feed.innerHTML;

    // Simulate the 5s history poll returning the same page: no jump, no DOM churn.
    app.sandbox.renderTranscript(messages);
    assert.equal(feed.scrollTop, 420);
    assert.equal(feed.innerHTML, htmlAfterFirst);

    // A later poll that only appends a system chip still restores the same reading offset.
    const withChip = [
        ...messages,
        { id: "sys1", lane: "system", name: "system", text: "Heartbeat", timestamp: 31, event: { kind: "heartbeat" } },
    ];
    feed.scrollTop = 420;
    const mid = {
        offsetTop: 400,
        offsetHeight: 200,
        getAttribute: name => (name === "data-id" ? "m2" : null),
    };
    const latest = {
        offsetTop: 900,
        offsetHeight: 120,
        getAttribute: name => (name === "data-id" ? "m3" : null),
    };
    feed.querySelectorAll = selector => {
        if (selector.includes("chat-msg") || selector.includes("system-event")) {
            return [mid, latest];
        }
        return [];
    };
    feed.querySelector = selector => {
        if (selector.includes("m2")) return mid;
        if (selector.includes("m3")) return latest;
        return null;
    };

    app.sandbox.renderTranscript(withChip);
    assert.equal(feed.scrollTop, 420);

    // Jump-to-latest remains an explicit control, not a poll side-effect.
    app.sandbox.bindJumpToLatestMessageControl();
    app.sandbox.updateJumpToLatestButton();
    assert.equal(button.hidden, false);
    assert.equal(feed.scrollTop, 420);
});

test("legacy interface font preference migrates to the split typography settings", () => {
    const app = loadFrontend({
        storage: {
            vc_settings: JSON.stringify({
                settingsVersion: 2,
                fontSize: "large",
                historyLimit: 30,
                defaultAgent: "claude",
                autoNarrate: false
            })
        }
    });

    const settings = vm.runInContext("getSettings()", app.sandbox);
    assert.equal(settings.settingsVersion, 5);
    assert.equal(settings.systemFontSize, "large");
    assert.equal(settings.chatFontFamily, "outfit");
    assert.equal(settings.chatFontSize, 18);

    app.sandbox.applySettings(settings);
    assert.equal(app.sandbox.document.body.classList.contains("font-large"), true);
    assert.equal(app.sandbox.document.body.style.getPropertyValue("--system-font-size"), "26px");
    assert.equal(app.sandbox.document.body.style.getPropertyValue("--chat-font-size"), "18px");
});

test("system and chat typography preview immediately, revert on close, and persist on save", () => {
    const app = loadFrontend();
    app.sandbox.applySettings();
    app.sandbox.initSettingsModal();

    const open = app.sandbox.document.getElementById("btn-open-settings");
    const close = app.sandbox.document.getElementById("btn-close-settings");
    const save = app.sandbox.document.getElementById("btn-save-settings");
    const systemSize = app.sandbox.document.getElementById("setting-system-font-size");
    const chatFamily = app.sandbox.document.getElementById("setting-chat-font-family");
    const chatSize = app.sandbox.document.getElementById("setting-chat-font-size");
    const lineHeight = app.sandbox.document.getElementById("setting-chat-line-height");
    const paragraphSpacing = app.sandbox.document.getElementById("setting-chat-paragraph-spacing");
    const fontWeight = app.sandbox.document.getElementById("setting-chat-font-weight");
    const letterSpacing = app.sandbox.document.getElementById("setting-chat-letter-spacing");

    open.listeners.click();
    systemSize.value = "xlarge";
    chatFamily.value = "georgia";
    chatSize.value = "24";
    lineHeight.value = "1.9";
    paragraphSpacing.value = "14";
    fontWeight.value = "500";
    letterSpacing.value = "0.4";
    lineHeight.listeners.input();

    const bodyStyle = app.sandbox.document.body.style;
    assert.equal(bodyStyle.getPropertyValue("--system-font-size"), "35px");
    assert.equal(bodyStyle.getPropertyValue("--chat-font-family"), "Georgia, 'Times New Roman', serif");
    assert.equal(bodyStyle.getPropertyValue("--chat-font-size"), "24px");
    assert.equal(bodyStyle.getPropertyValue("--chat-line-height"), "1.9");
    assert.equal(bodyStyle.getPropertyValue("--chat-paragraph-spacing"), "14px");
    assert.equal(bodyStyle.getPropertyValue("--chat-font-weight"), "500");
    assert.equal(bodyStyle.getPropertyValue("--chat-letter-spacing"), "0.4px");
    assert.equal(app.storage.vc_settings, undefined);

    close.listeners.click();
    assert.equal(bodyStyle.getPropertyValue("--system-font-size"), "20px");
    assert.equal(bodyStyle.getPropertyValue("--chat-font-size"), "18px");

    open.listeners.click();
    systemSize.value = "xlarge";
    chatFamily.value = "georgia";
    chatSize.value = "24";
    lineHeight.value = "1.9";
    paragraphSpacing.value = "14";
    fontWeight.value = "500";
    letterSpacing.value = "0.4";
    chatSize.listeners.input();
    save.listeners.click();

    const persisted = JSON.parse(app.storage.vc_settings);
    assert.equal(persisted.settingsVersion, 5);
    assert.equal(persisted.systemFontSize, "xlarge");
    assert.equal(persisted.chatFontFamily, "georgia");
    assert.equal(persisted.chatFontSize, 24);
    assert.equal(persisted.chatLineHeight, 1.9);
    assert.equal(bodyStyle.getPropertyValue("--system-font-size"), "35px");
});

test("color themes preview immediately, revert on close, and persist on save", () => {
    const app = loadFrontend();
    app.sandbox.applySettings();
    app.sandbox.initSettingsModal();

    const open = app.sandbox.document.getElementById("btn-open-settings");
    const close = app.sandbox.document.getElementById("btn-close-settings");
    const save = app.sandbox.document.getElementById("btn-save-settings");
    const theme = app.sandbox.document.getElementById("setting-theme");

    assert.equal(app.sandbox.document.body.getAttribute("data-theme"), "midnight");
    assert.equal(app.sandbox.document.body.style.getPropertyValue("color-scheme"), "dark");

    open.listeners.click();
    theme.value = "quiet-light";
    theme.listeners.change();
    assert.equal(app.sandbox.document.body.getAttribute("data-theme"), "quiet-light");
    assert.equal(app.sandbox.document.body.style.getPropertyValue("color-scheme"), "light");
    assert.equal(app.storage.vc_settings, undefined);

    close.listeners.click();
    assert.equal(app.sandbox.document.body.getAttribute("data-theme"), "midnight");

    open.listeners.click();
    theme.value = "high-contrast";
    theme.listeners.change();
    save.listeners.click();

    const persisted = JSON.parse(app.storage.vc_settings);
    assert.equal(persisted.settingsVersion, 5);
    assert.equal(persisted.theme, "high-contrast");
    assert.equal(app.sandbox.document.body.getAttribute("data-theme"), "high-contrast");
    assert.equal(app.sandbox.document.body.style.getPropertyValue("color-scheme"), "dark");

    open.listeners.click();
    theme.value = "paper-light";
    theme.listeners.change();
    assert.equal(app.sandbox.document.body.style.getPropertyValue("color-scheme"), "light");
    close.listeners.click();

    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");
    const cssContent = fs.readFileSync(path.join(__dirname, "../frontend/index.css"), "utf8");
    for (const themeId of [
        "paper-light", "solarized-light", "rose-pine-dawn", "mist-light", "sepia-light",
        "dracula", "one-dark", "solarized-dark", "tokyo-night", "catppuccin-mocha"
    ]) {
        assert.ok(htmlContent.includes(`value="${themeId}"`), `missing theme option ${themeId}`);
        assert.ok(cssContent.includes(`body[data-theme="${themeId}"]`), `missing theme CSS ${themeId}`);
    }
});

test("composer send control is not a confirmation gate", () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");

    assert.equal(input.readOnly, false);
    assert.ok(htmlContent.includes('id="btn-send"'));
    assert.ok(htmlContent.includes("<span>Send</span>"));
    assert.equal(htmlContent.includes("confirmation-gate"), false);
    assert.equal(htmlContent.includes("Draft Message"), false);
});

test("quick suggestions expose smart AI chips, More menu, and most-used predefined message", () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");

    const cssContent = fs.readFileSync(path.join(__dirname, "../frontend/index.css"), "utf8");

    assert.match(htmlContent, /id="quick-suggestions"/);
    assert.match(htmlContent, /id="quick-suggestion-smart-0"/);
    assert.match(htmlContent, /id="quick-suggestion-smart-1"/);
    assert.match(htmlContent, /id="btn-more-quick-suggestions"/);
    assert.match(htmlContent, /id="quick-suggestions-menu"/);
    assert.match(htmlContent, /data-quick-suggestion="executive-summary"/);

    assert.match(cssContent, /\.composer-container[\s\S]*?overflow:\s*visible/);
    assert.match(cssContent, /\.quick-suggestions-menu[\s\S]*?z-index:\s*1000/);
    assert.match(cssContent, /\.quick-suggestion-smart/);

    vm.runInContext('activeRoomId = "room-quick-suggestions";', app.sandbox);
    app.sandbox.applySmartQuickSuggestions("room-quick-suggestions", [
        { label: "Double-check", command: "@grok Independently double-check the implementation." },
        { label: "Your take", command: "@codex What is your opinion on the latest result?" }
    ]);
    assert.equal(app.sandbox.insertQuickSuggestion("smart-0"), true);
    assert.equal(input.value, "@grok Independently double-check the implementation.");
    assert.equal(
        app.sandbox.document.getElementById("quick-suggestion-smart-0").textContent,
        "Double-check"
    );

    input.value = "";
    assert.equal(app.sandbox.insertQuickSuggestion("status"), true);
    assert.equal(input.value, "What is the status of the current task?");
    assert.equal(
        vm.runInContext('getRoomState("room-quick-suggestions").lastInsertedSuggestion', app.sandbox),
        undefined
    );
    assert.equal(app.storage.vc_quick_suggestion_usage, JSON.stringify({ status: 1 }));

    input.value = "";
    assert.equal(app.sandbox.insertQuickSuggestion("most-used"), true);
    assert.equal(input.value, "What is the status of the current task?");
    assert.equal(app.storage.vc_quick_suggestion_usage, JSON.stringify({ status: 2 }));

    input.value = "";
    assert.equal(app.sandbox.insertQuickSuggestion("executive-summary"), true);
    assert.match(input.value, /^Give an executive summary of where we are in the overall plan/);
    assert.match(input.value, /Omit code, file changes, commits, classes, tests, and routine implementation details\.$/);
});

test("browser composer sends through gateway contracts without a confirmation dialog", async () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const requests = [];
    input.value = "@codex gateway browser message";
    vm.runInContext('activeRoomId = "room-gateway";', app.sandbox);

    app.sandbox.fetch = (url, options) => {
        requests.push({ url, options });
        if (url === "/api/gateway/interact") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    status: "awaiting_confirmation",
                    confirmation_snapshot: {
                        schema_version: "1.0",
                        immutable_interaction_id: "int-browser",
                        room_id: "room-gateway",
                        agent: "codex",
                        exact_message: "@codex gateway browser message",
                        permission_tier: "commit",
                        expires_at: Date.now() / 1000 + 60,
                        nonce: "nonce-browser"
                    }
                })
            });
        }
        return Promise.resolve({
            ok: true,
            json: async () => ({ status: "posted", rocket_chat_msg_ids: ["rc-browser"] })
        });
    };

    await app.sandbox.sendMessage();
    assert.equal(requests[0].url, "/api/gateway/interact");
    assert.equal(requests[1].url, "/api/gateway/confirm");
    assert.equal(JSON.parse(requests[1].options.body).nonce, "nonce-browser");
    assert.equal(input.value, "");
});

test("pressing Enter sends the composer and Shift+Enter remains multiline", async () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const requests = [];
    vm.runInContext('activeRoomId = "room-enter";', app.sandbox);
    input.value = "@codex send with Enter";
    app.sandbox.fetch = (url, options) => {
        requests.push({ url, options });
        if (url === "/api/gateway/interact") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    confirmation_snapshot: {
                        schema_version: "1.0",
                        immutable_interaction_id: "int-enter",
                        room_id: "room-enter",
                        agent: "codex",
                        exact_message: "@codex send with Enter",
                        permission_tier: "commit",
                        expires_at: Date.now() / 1000 + 60,
                        nonce: "nonce-enter"
                    }
                })
            });
        }
        return Promise.resolve({
            ok: true,
            json: async () => ({ status: "posted", rocket_chat_msg_ids: ["rc-enter"] })
        });
    };

    const preventDefault = () => {};
    await input.listeners.keydown({ key: "Enter", shiftKey: false, preventDefault });
    assert.deepEqual(requests.slice(0, 2).map(request => request.url), [
        "/api/gateway/interact",
        "/api/gateway/confirm"
    ]);
    assert.equal(input.value, "");

    input.value = "keep\nwriting";
    await input.listeners.keydown({ key: "Enter", shiftKey: true, preventDefault });
    assert.equal(input.value, "keep\nwriting");
});

test("recent stats bar renders formatted agent metrics", () => {
    const app = loadFrontend();
    const statsBar = app.sandbox.document.getElementById("stats-bar");

    const stats = {
        codex: {
            runs: 3,
            avg_response_time: 2.5,
            msg_count: 12
        }
    };

    vm.runInContext('renderStats(' + JSON.stringify(stats) + ');', app.sandbox);

    assert.equal(statsBar.style.display, "flex");
    assert.equal(statsBar.innerHTML.includes("Codex"), true);
    assert.equal(statsBar.innerHTML.includes("3 runs"), true);
    assert.equal(statsBar.innerHTML.includes("12 msgs"), true);
});

test("combined response assistant puts a suggestion in an empty composer without sending", async () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const requests = [];
    vm.runInContext(`
        activeRoomId = "room-assist";
        roomsList = [{ id: "room-assist", name: "voice_channel" }];
    `, app.sandbox);

    app.sandbox.fetch = (url, options) => {
        requests.push({ url, options });
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [{
                        id: "agent-new",
                        lane: "agent",
                        username: "acli_bot",
                        name: "ACLI Bot",
                        text: "**@agy**: implementation complete",
                        timestamp: "2026-07-30T12:00:00Z",
                        event: { kind: "agent_response", agent: "agy" }
                    }]
                })
            });
        }
        if (url === "/api/response-assistant") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    digest: "AGY completed the work.\n\nGrok should review it.",
                    phase: "review",
                    suggested_message: "@grok Please independently review AGY's implementation.",
                    trigger_message_id: "agent-new",
                    included_message_ids: ["agent-new"]
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.handleGenerateDigest({ autoPlay: false, triggerMessageId: "agent-new" });

    assert.equal(input.value, "@grok Please independently review AGY's implementation.");
    assert.equal(requests.some(request => request.url === "/api/response-assistant"), true);
    assert.equal(requests.some(request => request.url === "/api/gateway/confirm"), false);
    assert.equal(requests.some(request => request.url.includes("count=100")), true);
    const requestBody = JSON.parse(requests.find(request => request.url === "/api/response-assistant").options.body);
    assert.equal(requestBody.room_name, "voice_channel");
    assert.equal(requestBody.trigger_message_id, "agent-new");
});

test("AI suggestion never overwrites Ed's existing draft", () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    input.value = "@claude Ed is already editing this";
    vm.runInContext(`
        activeRoomId = "room-preserve";
        getRoomState("room-preserve").draftText = "@claude Ed is already editing this";
    `, app.sandbox);

    const inserted = vm.runInContext(
        'applySuggestedDraft("room-preserve", "@grok Generated suggestion")',
        app.sandbox
    );

    assert.equal(inserted, false);
    assert.equal(input.value, "@claude Ed is already editing this");
});

test("AI suggestion never changes draft state while confirmation locks the composer", () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    input.value = "@codex exact message awaiting confirmation";
    input.readOnly = true;
    vm.runInContext(`
        activeRoomId = "room-confirming";
        const state = getRoomState("room-confirming");
        state.draftText = "@codex exact message awaiting confirmation";
        state.lastInsertedSuggestion = "@codex exact message awaiting confirmation";
    `, app.sandbox);

    const inserted = vm.runInContext(
        'applySuggestedDraft("room-confirming", "@grok A newer generated suggestion")',
        app.sandbox
    );

    assert.equal(inserted, false);
    assert.equal(input.value, "@codex exact message awaiting confirmation");
    assert.equal(
        vm.runInContext('getRoomState("room-confirming").draftText', app.sandbox),
        "@codex exact message awaiting confirmation"
    );
});

test("polling ignores the initial backlog and auto-assists only a newly arrived real response", async () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const requests = [];
    let pollNumber = 0;
    vm.runInContext(`
        activeRoomId = "room-auto";
        roomsList = [{ id: "room-auto", name: "voice_channel" }];
        localStorage.setItem("vc_settings", JSON.stringify({
            settingsVersion: 2,
            fontSize: "medium",
            historyLimit: 20,
            defaultAgent: "codex",
            autoNarrate: true
        }));
    `, app.sandbox);

    app.sandbox.fetch = (url, options) => {
        requests.push({ url, options });
        if (url.startsWith("/api/history")) {
            pollNumber += 1;
            const messages = [{
                id: "user-existing",
                lane: "user",
                username: "ed",
                text: "Existing request",
                timestamp: "2026-07-30T11:00:00Z",
                event: { kind: "user_message" }
            }];
            if (pollNumber >= 2) {
                messages.push({
                    id: "agent-arrived",
                    lane: "agent",
                    username: "acli_bot",
                    text: "**@agy**: done",
                    timestamp: "2026-07-30T12:00:00Z",
                    event: { kind: "agent_response", agent: "agy" }
                });
            }
            return Promise.resolve({
                ok: true,
                json: async () => ({ success: true, messages, has_more: false, stats: {} })
            });
        }
        if (url === "/api/response-assistant") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    digest: "AGY completed the task.\n\nThe implementation now needs review.",
                    phase: "review",
                    suggested_message: "@grok Review AGY's completed task.",
                    trigger_message_id: "agent-arrived",
                    included_message_ids: ["user-existing", "agent-arrived"]
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.loadHistory();
    assert.equal(requests.filter(request => request.url === "/api/response-assistant").length, 0);

    await app.sandbox.loadHistory();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(requests.filter(request => request.url === "/api/response-assistant").length, 1);
    assert.equal(input.value, "@grok Review AGY's completed task.");
});

test("two tabs produce at most one automatic request, saved draft, and narration", async () => {
    const sharedStorage = {};
    const firstTab = loadFrontend({ storage: sharedStorage, enableSpeech: true });
    const secondTab = loadFrontend({ storage: sharedStorage, enableSpeech: true });
    const requests = [];
    const agentResponse = {
        id: "agent-cross-tab",
        lane: "agent",
        username: "acli_bot",
        text: "**@agy**: implementation complete",
        timestamp: "2026-07-30T12:00:00Z",
        event: { kind: "agent_response", agent: "agy" }
    };

    for (const app of [firstTab, secondTab]) {
        vm.runInContext(`
            activeRoomId = "room-cross-tab";
            roomsList = [{ id: "room-cross-tab", name: "voice_channel" }];
        `, app.sandbox);
        app.sandbox.fetch = (url, options) => {
            requests.push({ url, options });
            if (url.startsWith("/api/history")) {
                return Promise.resolve({
                    ok: true,
                    json: async () => ({ success: true, messages: [agentResponse] })
                });
            }
            if (url === "/api/response-assistant") {
                return Promise.resolve({
                    ok: true,
                    json: async () => ({
                        digest: "AGY completed the work.\n\nGrok should review it.",
                        phase: "review",
                        suggested_message: "@grok Review AGY's completed work.",
                        trigger_message_id: "agent-cross-tab",
                        included_message_ids: ["agent-cross-tab"],
                        automatic_action_allowed: true,
                        idempotency_replayed: false
                    })
                });
            }
            throw new Error("Unexpected request: " + url);
        };
    }

    await Promise.all([
        firstTab.sandbox.handleGenerateDigest({
            autoPlay: true,
            triggerMessageId: "agent-cross-tab"
        }),
        secondTab.sandbox.handleGenerateDigest({
            autoPlay: true,
            triggerMessageId: "agent-cross-tab"
        })
    ]);

    assert.equal(requests.filter(request => request.url === "/api/response-assistant").length, 1);
    assert.equal(
        [firstTab, secondTab].filter(app => (
            app.sandbox.document.getElementById("command-input").value
            === "@grok Review AGY's completed work."
        )).length,
        1
    );
    assert.equal(
        [...firstTab.storageWrites, ...secondTab.storageWrites]
            .filter(write => write.key === "vc_draft_room-cross-tab").length,
        1
    );
    assert.equal(firstTab.spoken.length + secondTab.spoken.length, 0);
});

test("backend suppression stays authoritative when cross-tab storage is unavailable", async () => {
    const app = loadFrontend({ enableSpeech: true });
    vm.runInContext(`
        activeRoomId = "room-replay";
        roomsList = [{ id: "room-replay", name: "voice_channel" }];
    `, app.sandbox);
    app.sandbox.localStorage.getItem = () => { throw new Error("storage unavailable"); };
    app.sandbox.localStorage.setItem = () => { throw new Error("storage unavailable"); };
    app.sandbox.localStorage.removeItem = () => { throw new Error("storage unavailable"); };
    app.sandbox.fetch = (url) => {
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [{
                        id: "agent-replayed",
                        lane: "agent",
                        text: "already handled",
                        event: { kind: "agent_response", agent: "agy" }
                    }]
                })
            });
        }
        if (url === "/api/response-assistant") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    digest: "Duplicate digest.\n\nDuplicate next step.",
                    suggested_message: "@grok Duplicate draft.",
                    trigger_message_id: "agent-replayed",
                    automatic_action_allowed: false,
                    idempotency_replayed: true
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.handleGenerateDigest({
        autoPlay: true,
        triggerMessageId: "agent-replayed"
    });

    assert.equal(app.sandbox.document.getElementById("command-input").value, "@grok Duplicate draft.");
    assert.equal(app.spoken.length, 0);
    assert.equal(
        app.storageWrites.filter(write => write.key === "vc_draft_room-replay").length,
        0
    );
});

test("in-flight lease claim is touched during long requests to prevent expiry", async () => {
    const app = loadFrontend();
    const key = app.sandbox.automaticAssistantStorageKey("lease", "room-long-request", "msg-long");

    vm.runInContext(`
        activeRoomId = "room-long-request";
        roomsList = [{ id: "room-long-request", name: "voice_channel" }];
    `, app.sandbox);

    app.sandbox.tryClaimAutomaticAssistance("room-long-request", "msg-long");
    const initialLease = JSON.parse(app.storage[key]);

    app.sandbox.touchAutomaticAssistanceClaim("room-long-request", "msg-long");
    const touchedLease = JSON.parse(app.storage[key]);

    assert.ok(touchedLease.expiresAt >= initialLease.expiresAt);
});

test("an expired winner lease hands cached digest and draft to another tab without replaying audio", async () => {
    const sharedStorage = {};
    const firstTab = loadFrontend({ storage: sharedStorage, enableSpeech: true });
    const secondTab = loadFrontend({ storage: sharedStorage, enableSpeech: true });
    const agentResponse = {
        id: "agent-abandoned-tab",
        lane: "agent",
        username: "acli_bot",
        text: "**@agy**: implementation complete",
        timestamp: "2026-07-30T12:00:00Z",
        event: { kind: "agent_response", agent: "agy" }
    };
    const result = {
        digest: "AGY completed the implementation.\n\nGrok should review it.",
        phase: "review",
        suggested_message: "@grok Review the recovered implementation result.",
        trigger_message_id: "agent-abandoned-tab",
        included_message_ids: ["agent-abandoned-tab"]
    };
    let resolveFirstResponse;
    const firstResponse = new Promise(resolve => { resolveFirstResponse = resolve; });
    let assistantRequests = 0;

    for (const app of [firstTab, secondTab]) {
        vm.runInContext(`
            activeRoomId = "room-abandoned-tab";
            roomsList = [{ id: "room-abandoned-tab", name: "voice_channel" }];
        `, app.sandbox);
        app.sandbox.fetch = (url) => {
            if (url.startsWith("/api/history")) {
                return Promise.resolve({
                    ok: true,
                    json: async () => ({ success: true, messages: [agentResponse] })
                });
            }
            if (url === "/api/response-assistant") {
                assistantRequests += 1;
                if (assistantRequests === 1) return firstResponse;
                return Promise.resolve({
                    ok: true,
                    json: async () => ({
                        ...result,
                        automatic_action_allowed: false,
                        idempotency_replayed: true
                    })
                });
            }
            throw new Error("Unexpected request: " + url);
        };
    }

    const abandonedRun = firstTab.sandbox.handleGenerateDigest({
        autoPlay: true,
        triggerMessageId: "agent-abandoned-tab"
    });
    await new Promise(resolve => setImmediate(resolve));

    const leaseKey = Object.keys(sharedStorage).find(key => (
        key.startsWith("vc_response_assistant_lease_")
    ));
    assert.ok(leaseKey);
    const expiredLease = JSON.parse(sharedStorage[leaseKey]);
    expiredLease.expiresAt = 0;
    sharedStorage[leaseKey] = JSON.stringify(expiredLease);

    await secondTab.sandbox.handleGenerateDigest({
        autoPlay: true,
        triggerMessageId: "agent-abandoned-tab"
    });

    resolveFirstResponse({
        ok: true,
        json: async () => ({
            ...result,
            automatic_action_allowed: true,
            idempotency_replayed: false
        })
    });
    await abandonedRun;

    assert.equal(assistantRequests, 2);
    assert.equal(
        secondTab.sandbox.document.getElementById("command-input").value,
        "@grok Review the recovered implementation result."
    );
    assert.equal(firstTab.sandbox.document.getElementById("command-input").value, "");
    assert.equal(
        [...firstTab.storageWrites, ...secondTab.storageWrites]
            .filter(write => write.key === "vc_draft_room-abandoned-tab").length,
        1
    );
    assert.equal(firstTab.spoken.length + secondTab.spoken.length, 0);
    assert.equal(
        secondTab.sandbox.document.getElementById("assistant-status").innerHTML.includes(
            "Automatic: recovered draft ready; press Play for audio"
        ),
        true
    );
});

test("a transient response-assistant failure is retried from the next poll after backoff", async () => {
    const app = loadFrontend();
    const requests = [];
    const agentResponse = {
        id: "agent-retry",
        lane: "agent",
        username: "acli_bot",
        text: "**@agy**: done",
        timestamp: "2026-07-30T12:00:00Z",
        event: { kind: "agent_response", agent: "agy" }
    };
    vm.runInContext(`
        activeRoomId = "room-retry";
        roomsList = [{ id: "room-retry", name: "voice_channel" }];
        const state = getRoomState("room-retry");
        state.pollInitialized = true;
        state.messageMap["agent-retry"] = ${JSON.stringify(agentResponse)};
        state.orderedIds = ["agent-retry"];
        state.failedAssistantId = "agent-retry";
        state.assistantRetryAt = 0;
        localStorage.setItem("vc_settings", JSON.stringify({
            settingsVersion: 2,
            fontSize: "medium",
            historyLimit: 20,
            defaultAgent: "codex",
            autoNarrate: true
        }));
    `, app.sandbox);

    app.sandbox.fetch = (url, options) => {
        requests.push({ url, options });
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [agentResponse],
                    has_more: false,
                    stats: {}
                })
            });
        }
        if (url === "/api/response-assistant") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    digest: "AGY completed the work.\n\nGrok should review it.",
                    phase: "review",
                    suggested_message: "@grok Review AGY's completed work.",
                    trigger_message_id: "agent-retry",
                    included_message_ids: ["agent-retry"]
                })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.loadHistory();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(requests.filter(request => request.url === "/api/response-assistant").length, 1);
    assert.equal(
        vm.runInContext('getRoomState("room-retry").lastAssistedId', app.sandbox),
        "agent-retry"
    );
});

test("attention rail uses position for ranked priority without printing rank or score", async () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "r1", name: "voice_channel" },
            { id: "r2", name: "JobHunting" },
            { id: "r3", name: "unconfigured_room" }
        ];
        activeRoomId = "r1";
    `, app.sandbox);

    app.sandbox.fetch = (url) => {
        if (url === "/api/attention/queue") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    queue: [
                        {
                            channel_name: "voice_channel",
                            queue_category: "ranked",
                            rank: 1,
                            score: 125.0,
                            factors: { base_importance_points: 100 }
                        },
                        {
                            channel_name: "JobHunting",
                            queue_category: "busy",
                            working_elapsed_seconds: 300
                        },
                        {
                            channel_name: "unconfigured_room",
                            queue_category: "unconfigured"
                        }
                    ]
                })
            });
        }
        throw new Error("Unexpected fetch URL: " + url);
    };

    await app.sandbox.fetchAttentionQueue(true);
    const html = app.sandbox.document.getElementById("channels-list").innerHTML;
    assert.equal(html.includes("attn-badge-ready"), true);
    assert.equal(html.includes("attn-ready-dot"), true);
    assert.equal(html.includes("#1 · 125"), false);
    assert.ok(html.includes("attn-badge-busy"));
    assert.ok(html.includes("Busy 5m"));
    assert.ok(html.includes("attn-badge-unconfigured"));
});

test("real attention updates repaint the rail even while composer text is active", async () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [{ id: "r1", name: "voice_channel" }];
        activeRoomId = "r1";
        commandInput.value = "Drafting instruction for agent...";
    `, app.sandbox);

    app.sandbox.fetch = (url) => {
        if (url === "/api/attention/queue") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    queue: [{ channel_name: "voice_channel", queue_category: "ranked", rank: 1, score: 200 }]
                })
            });
        }
        throw new Error("Unexpected fetch URL: " + url);
    };

    await app.sandbox.fetchAttentionQueue(false);
    assert.equal(vm.runInContext("attentionQueueData[0].score", app.sandbox), 200);
    assert.ok(app.sandbox.document.getElementById("channels-list").innerHTML.includes("voice_channel"));
});

test("clearing the composer is not required to apply an attention update", async () => {
    const app = loadFrontend();
    const command = app.sandbox.document.getElementById("command-input");
    vm.runInContext(`
        roomsList = [{ id: "r1", name: "voice_channel" }];
        activeRoomId = "r1";
        commandInput.value = "Drafting instruction for agent...";
    `, app.sandbox);

    app.sandbox.fetch = (url) => {
        if (url === "/api/attention/queue") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    queue: [{ channel_name: "voice_channel", queue_category: "ranked", rank: 1, score: 200 }]
                })
            });
        }
        throw new Error("Unexpected fetch URL: " + url);
    };

    await app.sandbox.fetchAttentionQueue(false);
    assert.equal(vm.runInContext("attentionQueueData[0].score", app.sandbox), 200);

    command.value = "";
    command.listeners.input({ target: command });

    assert.equal(vm.runInContext("attentionQueueData[0].score", app.sandbox), 200);
    assert.equal(app.sandbox.document.getElementById("channels-list").innerHTML.includes("#1 · 200"), false);
});

test("attention rail sorts by real conversation recency except explicit priority overrides", () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "u-new", name: "unconfigured_new", lm: "2026-08-02T12:00:00Z" },
            { id: "c-old", name: "configured_old", lm: "2026-08-01T08:00:00Z" },
            { id: "c-new", name: "configured_new", lm: "2026-08-02T10:00:00Z" },
            { id: "c-critical", name: "configured_critical", lm: "2026-07-01T11:00:00Z" },
            { id: "u-old", name: "unconfigured_old", lm: "2026-07-30T08:00:00Z" }
        ];
        attentionQueueData = [
            { channel_name: "configured_old", configured: true, queue_category: "ranked", score: 900, last_real_message_at: 100 },
            { channel_name: "configured_new", configured: true, queue_category: "ranked", score: 1, last_real_message_at: 200 },
            { channel_name: "configured_critical", configured: true, queue_category: "ranked", score: 5, last_real_message_at: 50, priority_override: true },
            { channel_name: "unconfigured_new", configured: false, queue_category: "unconfigured", score: null, last_real_message_at: 500 },
            { channel_name: "unconfigured_old", configured: false, queue_category: "unconfigured", score: null, last_real_message_at: 400 }
        ];
        renderChannelsList();
    `, app.sandbox);

    const html = app.sandbox.document.getElementById("channels-list").innerHTML;
    const order = [
        "configured_critical",
        "configured_old",
        "configured_new",
        "unconfigured_new",
        "unconfigured_old"
    ].map(name => html.indexOf(`data-channel-name="${name}"`));
    assert.ok(order.every(index => index >= 0));
    assert.deepEqual([...order].sort((a, b) => a - b), order);
});

test("unseen real activity rises above viewed importance until the room is opened", () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "imp", name: "important_reviewed" },
            { id: "fresh", name: "fresh_update" }
        ];
        attentionQueueData = [
            { channel_name: "important_reviewed", configured: true, queue_category: "ranked", score: 900, last_real_message_at: 50, has_unseen_real_activity: false },
            { channel_name: "fresh_update", configured: true, queue_category: "ranked", score: 10, last_real_message_at: 500, has_unseen_real_activity: true }
        ];
        renderChannelsList();
    `, app.sandbox);
    let html = app.sandbox.document.getElementById("channels-list").innerHTML;
    assert.ok(html.indexOf('data-channel-name="fresh_update"') < html.indexOf('data-channel-name="important_reviewed"'));

    vm.runInContext(`
        attentionQueueData[1].has_unseen_real_activity = false;
        lastChannelsListSignature = "";
        renderChannelsList();
    `, app.sandbox);
    html = app.sandbox.document.getElementById("channels-list").innerHTML;
    assert.ok(html.indexOf('data-channel-name="important_reviewed"') < html.indexOf('data-channel-name="fresh_update"'));
});

test("a room-level New badge outranks reviewed and Busy queue items", () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "reviewed", name: "important_reviewed" },
            { id: "busy", name: "busy_worker" },
            {
                id: "config-only",
                name: "config_only_new",
                has_unread: true,
                last_real_message_at: 500
            }
        ];
        attentionQueueData = [
            { channel_name: "important_reviewed", queue_category: "ranked", score: 900, has_unread: false },
            { channel_name: "busy_worker", queue_category: "busy", last_real_message_at: 600, has_unread: false },
            { channel_name: "config_only_new", queue_category: "unknown", score: null, has_unread: false, has_unseen_real_activity: false }
        ];
        renderChannelsList();
    `, app.sandbox);

    const html = app.sandbox.document.getElementById("channels-list").innerHTML;
    const newIndex = html.indexOf('data-channel-name="config_only_new"');
    const busyIndex = html.indexOf('data-channel-name="busy_worker"');
    const reviewedIndex = html.indexOf('data-channel-name="important_reviewed"');
    assert.ok(newIndex >= 0 && busyIndex >= 0 && reviewedIndex >= 0);
    assert.ok(newIndex < busyIndex);
    assert.ok(newIndex < reviewedIndex);
    assert.ok(html.includes('title="Unreviewed real activity in this channel">New</span>'));
});

test("channel rail skips a second paint when the payload is unchanged", () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "c-new", name: "configured_new" },
            { id: "c-old", name: "configured_old" }
        ];
        attentionQueueData = [
            { channel_name: "configured_new", configured: true, queue_category: "ranked", last_real_message_at: 200 },
            { channel_name: "configured_old", configured: true, queue_category: "ranked", last_real_message_at: 100 }
        ];
        renderChannelsList();
    `, app.sandbox);

    const list = app.sandbox.document.getElementById("channels-list");
    const firstHtml = list.innerHTML;
    assert.ok(firstHtml.includes('data-channel-name="configured_new"'));
    list.innerHTML = "MUTATED";
    vm.runInContext("renderChannelsList();", app.sandbox);
    assert.equal(list.innerHTML, "MUTATED");
});

test("recent stats bar is not rewritten when the stats payload is unchanged", () => {
    const app = loadFrontend();
    const stats = { codex: { runs: 2, avg_response_time: 5, msg_count: 3, status: "idle" } };
    app.sandbox.__stats = stats;
    vm.runInContext("renderStats(__stats);", app.sandbox);

    const bar = app.sandbox.document.getElementById("stats-bar");
    assert.ok(bar.innerHTML.includes("Recent Window Stats"));
    bar.innerHTML = "MUTATED";
    vm.runInContext("renderStats(__stats);", app.sandbox);
    assert.equal(bar.innerHTML, "MUTATED");
    assert.equal(bar.style.display, "flex");

    app.sandbox.__stats2 = { codex: { runs: 3, avg_response_time: 5, msg_count: 4, status: "idle" } };
    vm.runInContext("renderStats(__stats2);", app.sandbox);
    assert.ok(bar.innerHTML.includes("Recent Window Stats"));
});

test("room select options are not rebuilt when the room set is unchanged", async () => {
    const app = loadFrontend();
    const rooms = [{ id: "r1", name: "alpha" }, { id: "r2", name: "bravo" }];
    app.sandbox.fetch = (url) => Promise.resolve({
        ok: true,
        json: async () => url.startsWith("/api/rooms")
            ? { success: true, rooms }
            : { success: true, messages: [], queue: [] },
    });

    await vm.runInContext("loadRooms();", app.sandbox);
    const select = app.sandbox.document.getElementById("room-select");
    assert.ok(select.innerHTML.includes('value="r1"'));

    select.innerHTML = "MUTATED";
    await vm.runInContext("loadRooms();", app.sandbox);
    assert.equal(select.innerHTML, "MUTATED");

    rooms.push({ id: "r3", name: "charlie" });
    await vm.runInContext("loadRooms();", app.sandbox);
    assert.ok(select.innerHTML.includes('value="r3"'));
});

test("channel rail applies updated recency ordering immediately", () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "a", name: "alpha" },
            { id: "b", name: "bravo" }
        ];
        attentionQueueData = [
            { channel_name: "alpha", configured: true, queue_category: "ranked", last_real_message_at: 200 },
            { channel_name: "bravo", configured: true, queue_category: "ranked", last_real_message_at: 100 }
        ];
        renderChannelsList();
        attentionQueueData = [
            { channel_name: "alpha", configured: true, queue_category: "ranked", last_real_message_at: 50 },
            { channel_name: "bravo", configured: true, queue_category: "ranked", last_real_message_at: 300 }
        ];
        renderChannelsList();
    `, app.sandbox);

    const resorted = app.sandbox.document.getElementById("channels-list").innerHTML;
    const liveOrder = [
        "bravo",
        "alpha"
    ].map(name => resorted.indexOf(`data-channel-name="${name}"`));
    assert.ok(liveOrder.every(index => index >= 0));
    assert.deepEqual([...liveOrder].sort((a, b) => a - b), liveOrder);
});

test("failed attention fetch preserves newest-first room order", async () => {
    const app = loadFrontend();
    app.sandbox.fetch = (url) => {
        if (url === "/api/rooms") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    rooms: [
                        { id: "new", name: "zulu_recent", lm: "2026-08-16T20:00:00Z" },
                        { id: "old", name: "alpha_old", lm: "2026-08-15T20:00:00Z" }
                    ]
                })
            });
        }
        if (url === "/api/attention/queue") {
            return Promise.resolve({ ok: false, status: 500 });
        }
        return Promise.resolve({ ok: true, json: async () => ({ success: true }) });
    };
    vm.runInContext(`
        attentionQueueData = [
            { channel_name: "alpha_old", queue_category: "ranked", last_real_message_at: 999 }
        ];
    `, app.sandbox);

    await vm.runInContext("loadRooms();", app.sandbox);

    const html = app.sandbox.document.getElementById("channels-list").innerHTML;
    const recentIndex = html.indexOf('data-channel-name="zulu_recent"');
    const oldIndex = html.indexOf('data-channel-name="alpha_old"');
    assert.ok(recentIndex >= 0 && oldIndex >= 0);
    assert.ok(recentIndex < oldIndex);
    assert.equal(vm.runInContext("attentionQueueData.length", app.sandbox), 0);
});

test("composer text does not freeze event-driven attention queue updates", async () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [{ id: "r1", name: "voice_channel" }];
        activeRoomId = "r1";
        commandInput.value = "message in progress";
    `, app.sandbox);

    app.sandbox.fetch = (url) => {
        if (url === "/api/attention/queue") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    queue: [{ channel_name: "voice_channel", queue_category: "busy", working_elapsed_seconds: 120 }]
                })
            });
        }
        throw new Error("Unexpected fetch URL: " + url);
    };

    await app.sandbox.fetchAttentionQueue(false);
    assert.ok(app.sandbox.document.getElementById("channels-list").innerHTML.includes("Busy 2m"));
});

test("an older attention response cannot overwrite a newer event-driven queue", async () => {
    const app = loadFrontend();
    vm.runInContext(`
        roomsList = [
            { id: "older", name: "older_channel" },
            { id: "newer", name: "newer_channel" }
        ];
    `, app.sandbox);

    let releaseOlder;
    const olderResponse = new Promise(resolve => { releaseOlder = resolve; });
    let callCount = 0;
    app.sandbox.fetch = (url) => {
        if (url !== "/api/attention/queue") throw new Error("Unexpected fetch URL: " + url);
        callCount += 1;
        if (callCount === 1) return olderResponse;
        return Promise.resolve({
            ok: true,
            json: async () => ({
                success: true,
                queue: [{ channel_name: "newer_channel", queue_category: "ranked", score: 200 }]
            })
        });
    };

    const olderRequest = app.sandbox.fetchAttentionQueue(true);
    await app.sandbox.fetchAttentionQueue(true);
    releaseOlder({
        ok: true,
        json: async () => ({
            success: true,
            queue: [{ channel_name: "older_channel", queue_category: "ranked", score: 999 }]
        })
    });
    await olderRequest;

    assert.equal(vm.runInContext("attentionQueueData[0].channel_name", app.sandbox), "newer_channel");
});

test("gateway dispatch checklist UI elements exist in console settings", async () => {
    const app = loadFrontend();
    const heading = app.sandbox.document.getElementById("gateway-checklist-heading");
    const list = app.sandbox.document.getElementById("gateway-checklist-list");
    assert.ok(heading);
    assert.ok(list);

    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");
    assert.ok(htmlContent.includes("gateway-checklist-heading"));
    assert.ok(htmlContent.includes("voice_gateway"));
    assert.ok(htmlContent.includes("ed"));
});

test("openAttentionSettingsModal performs case-fold lookup and displays snooze retention option", async () => {
    const app = loadFrontend();
    const futureIso = new Date(Date.now() + 86400 * 1000).toISOString();
    app.sandbox.fetch = (url) => {
        if (url === "/api/attention/config") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    config: {
                        schema_version: "1.0",
                        channels: {
                            "Voice_Channel": {
                                attention_active: true,
                                base_importance: 4,
                                urgency: "high",
                                snoozed_until: futureIso
                            }
                        }
                    }
                })
            });
        }
        throw new Error("Unexpected fetch URL: " + url);
    };

    await app.sandbox.openAttentionSettingsModal("voice_channel");
    const channelInput = app.sandbox.document.getElementById("attn-channel-name");
    const keepOption = app.sandbox.document.getElementById("attn-snooze-keep-option");
    const snoozeSelect = app.sandbox.document.getElementById("attn-snooze-select");

    assert.equal(channelInput.dataset.canonicalName, "Voice_Channel");
    assert.equal(keepOption.style.display, "block");
    assert.equal(snoozeSelect.value, "keep");
});


test("attention settings modal is a top-level overlay, not nested inside the hidden settings modal", async () => {
    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");
    const voidTags = new Set(["br", "img", "input", "hr", "meta", "link", "source", "area", "base", "col", "embed", "param", "track", "wbr"]);
    const tagPattern = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g;

    const stack = [];
    let ancestorsOfAttentionModal = null;
    let ancestorsOfSettingsButton = null;
    let ancestorsOfChannelsResizeHandle = null;
    let match;
    while ((match = tagPattern.exec(htmlContent)) !== null) {
        const [, closing, tagName, attrs, selfClosing] = match;
        const tag = tagName.toLowerCase();
        if (closing) {
            const idx = stack.map(e => e.tag).lastIndexOf(tag);
            if (idx !== -1) stack.length = idx;
            continue;
        }
        const idMatch = /\bid\s*=\s*"([^"]*)"/.exec(attrs || "");
        const id = idMatch ? idMatch[1] : null;
        if (id === "attention-settings-modal" && ancestorsOfAttentionModal === null) {
            ancestorsOfAttentionModal = stack.map(e => e.id).filter(Boolean);
        }
        if (id === "btn-open-settings" && ancestorsOfSettingsButton === null) {
            ancestorsOfSettingsButton = stack.map(e => e.id).filter(Boolean);
        }
        if (id === "channels-resize-handle" && ancestorsOfChannelsResizeHandle === null) {
            ancestorsOfChannelsResizeHandle = stack.map(e => e.id).filter(Boolean);
        }
        if (!voidTags.has(tag) && !selfClosing) stack.push({ tag, id });
    }

    assert.notEqual(ancestorsOfAttentionModal, null, "attention-settings-modal must exist in index.html");
    assert.ok(
        !ancestorsOfAttentionModal.includes("settings-modal"),
        "attention-settings-modal must not be nested inside #settings-modal, which stays display:none and would hide it",
    );
    assert.ok(
        ancestorsOfSettingsButton.includes("channels-sidebar"),
        "console settings must remain in the channel sidebar footer",
    );
    assert.notEqual(ancestorsOfChannelsResizeHandle, null, "channel sidebar resize handle must exist");
    assert.deepEqual(stack.map(e => e.id).filter(Boolean), [], "index.html must have balanced tags at EOF");
});

test("agent-model-modal is a top-level overlay and opens cleanly without hidden parents", async () => {
    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");
    const tagPattern = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g;
    const voidTags = new Set(["br", "img", "input", "hr", "meta", "link", "source", "area", "base", "col", "embed", "param", "track", "wbr"]);

    const stack = [];
    let ancestorsOfAgentModelModal = null;
    let match;
    while ((match = tagPattern.exec(htmlContent)) !== null) {
        const [, closing, tagName, attrs, selfClosing] = match;
        const tag = tagName.toLowerCase();
        if (closing) {
            const idx = stack.map(e => e.tag).lastIndexOf(tag);
            if (idx !== -1) stack.length = idx;
            continue;
        }
        const idMatch = /\bid\s*=\s*"([^"]*)"/.exec(attrs || "");
        const id = idMatch ? idMatch[1] : null;
        if (id === "agent-model-modal" && ancestorsOfAgentModelModal === null) {
            ancestorsOfAgentModelModal = stack.map(e => e.id).filter(Boolean);
        }
        if (!voidTags.has(tag) && !selfClosing) stack.push({ tag, id });
    }

    assert.notEqual(ancestorsOfAgentModelModal, null, "agent-model-modal must exist in index.html");
    assert.ok(
        !ancestorsOfAgentModelModal.includes("attention-settings-modal"),
        "agent-model-modal must not be nested inside #attention-settings-modal",
    );
    assert.ok(
        !ancestorsOfAgentModelModal.includes("settings-modal"),
        "agent-model-modal must not be nested inside #settings-modal",
    );

    const app = loadFrontend();
    const modal = app.sandbox.document.getElementById("agent-model-modal");
    assert.ok(modal, "agent-model-modal element must be present in DOM");
    assert.equal(modal.style.display, "none", "modal starts hidden");

    app.sandbox.openAgentModelModal("codex");
    assert.equal(modal.style.display, "flex", "openAgentModelModal sets display:flex");
});

test("model toolbar is logo-only at rest and uses each logo as the selector trigger", () => {
    const htmlContent = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");
    const cssContent = fs.readFileSync(path.join(__dirname, "../frontend/index.css"), "utf8");

    for (const agent of ["codex", "agy", "claude", "grok"]) {
        assert.match(
            htmlContent,
            new RegExp(`id="btn-agent-${agent}"[^>]*class="[^"]*model-agent-control|class="[^"]*model-agent-control[^"]*"[^>]*id="btn-agent-${agent}"`),
        );
        assert.match(htmlContent, new RegExp(`id="btn-agent-${agent}"[^>]*draggable="true"`));
        assert.match(htmlContent, new RegExp(`id="model-label-${agent}"`));
    }
    assert.equal(htmlContent.includes('id="btn-agent-gemini"'), false);
    assert.equal(htmlContent.includes('id="model-label-gemini"'), false);
    assert.equal(htmlContent.includes("btn-tune-model"), false);
    assert.equal(htmlContent.includes("agent-model-chip"), false);
    assert.match(cssContent, /\.model-agent-control\s*\{[^}]*max-width:\s*38px/s);
    assert.match(cssContent, /\.model-agent-control:hover[^{]*\{[^}]*max-width:\s*240px/s);
});

test("dragged agent targets are inserted at the start of the composer draft", () => {
    const app = loadFrontend();
    vm.runInContext(`
        activeRoomId = "room-agent-drag";
        roomsList = [{ id: "room-agent-drag", name: "voice_channel" }];
    `, app.sandbox);
    const commandInput = app.sandbox.document.getElementById("command-input");

    commandInput.value = "what changed?";
    assert.equal(app.sandbox.insertAgentMentionIntoComposer("codex"), true);
    assert.equal(commandInput.value, "@codex what changed?");
    assert.equal(app.storage["vc_draft_room-agent-drag"], "@codex what changed?");
    assert.equal(
        vm.runInContext('getRoomState("room-agent-drag").draftText', app.sandbox),
        "@codex what changed?"
    );

    commandInput.value = "@claude review this branch";
    assert.equal(app.sandbox.insertAgentMentionIntoComposer("agy"), true);
    assert.equal(commandInput.value, "@agy review this branch");

    commandInput.readOnly = true;
    commandInput.value = "locked draft";
    assert.equal(app.sandbox.insertAgentMentionIntoComposer("grok"), false);
    assert.equal(commandInput.value, "locked draft");
});

test("model modal renders only the selected agent's complete ACLI catalog", async () => {
    const app = loadFrontend();
    vm.runInContext(`
        activeRoomId = "room-model-ui";
        roomsList = [{ id: "room-model-ui", name: "voice_channel" }];
    `, app.sandbox);
    const agents = {
        codex: {
            agent: "codex", available: true,
            current_model: "gpt-5.6-sol", current_effort: "high",
            display_model: "GPT 5.6 Sol", display_effort: "high",
            display_label: "Codex · GPT 5.6 Sol · high",
            available_models: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.4", "o3"],
            available_efforts: ["low", "medium", "high", "xhigh", "max"]
        },
        agy: { agent: "agy", available: true, current_model: "Gemini 3.5 Flash (High)", current_effort: "low", display_model: "Gemini 3.5 Flash (High)", display_effort: "low", available_models: ["Gemini 3.5 Flash (High)", "Gemini 3.1 Pro (High)"], available_efforts: ["low", "high"] },
        claude: {
            agent: "claude", available: true,
            current_model: "claude-sonnet-5", current_effort: "high",
            display_model: "Claude Sonnet 5", display_effort: "high",
            available_models: [
                "claude-sonnet-4-6", "claude-opus-4-8", "claude-opus-4-7",
                "claude-haiku-3-5", "sonnet", "opus", "haiku",
                "claude-sonnet-5", "claude-fable-5", "fable"
            ],
            available_efforts: ["low", "medium", "high", "xhigh", "max"]
        },
        grok: { agent: "grok", available: true, current_model: "grok-4.5", current_effort: "high", display_model: "Grok 4.5", display_effort: "high", available_models: ["grok-4.5", "grok-build"], available_efforts: ["low", "high", "xhigh"] }
    };
    app.sandbox.fetch = url => {
        assert.match(url, /roomId=room-model-ui/);
        assert.match(url, /channelName=voice_channel/);
        return Promise.resolve({
            ok: true,
            json: async () => ({
                success: true,
                room_id: "room-model-ui",
                channel_name: "voice_channel",
                agents
            })
        });
    };

    await app.sandbox.openAgentModelModal("codex");
    const modelSelect = app.sandbox.document.getElementById("model-select-input");
    const effortSelect = app.sandbox.document.getElementById("effort-select-input");
    assert.equal(modelSelect.disabled, false);
    assert.match(modelSelect.innerHTML, /gpt-5\.6-sol/);
    assert.match(modelSelect.innerHTML, /gpt-5\.6-terra/);
    assert.match(modelSelect.innerHTML, />o3</);
    assert.equal(modelSelect.innerHTML.includes("grok-4.5"), false);
    assert.equal(modelSelect.innerHTML.includes("Gemini 3.5 Flash"), false);
    assert.match(effortSelect.innerHTML, /value="xhigh"/);

    await app.sandbox.openAgentModelModal("claude");
    for (const model of agents.claude.available_models) {
        assert.ok(modelSelect.innerHTML.includes(model), `Claude selector must include ${model}`);
    }
    assert.equal(modelSelect.innerHTML.includes("gpt-5.6-sol"), false);
    for (const effort of ["low", "medium", "high", "xhigh", "max"]) {
        assert.ok(effortSelect.innerHTML.includes(`value="${effort}"`), `effort selector must include ${effort}`);
    }
});

test("AGY model families synchronize effort choices from ACLI slug metadata", async () => {
    const app = loadFrontend();
    vm.runInContext(`
        activeRoomId = "room-agy-model-ui";
        roomsList = [{ id: "room-agy-model-ui", name: "voice_channel" }];
    `, app.sandbox);
    const agy = {
        agent: "agy",
        available: true,
        current_model: "gemini-3.6-flash-low",
        current_effort: "low",
        selector_model: "gemini-3.6-flash",
        selector_effort: "low",
        display_model: "Gemini 3.6 Flash",
        display_effort: "low",
        available_models: [
            "gemini-3.6-flash-low", "gemini-3.6-flash-medium", "gemini-3.6-flash-high",
            "gemini-3.1-pro-low", "gemini-3.1-pro-high"
        ],
        available_efforts: ["low", "medium", "high", "xhigh", "max"],
        model_options: [
            { id: "gemini-3.6-flash", label: "Gemini 3.6 Flash", efforts: ["low", "medium", "high"] },
            { id: "gemini-3.1-pro", label: "Gemini 3.1 Pro", efforts: ["low", "high"] }
        ]
    };
    app.sandbox.fetch = url => {
        assert.match(url, /roomId=room-agy-model-ui/);
        return Promise.resolve({
            ok: true,
            json: async () => ({
                success: true,
                room_id: "room-agy-model-ui",
                channel_name: "voice_channel",
                agents: { agy }
            })
        });
    };

    await app.sandbox.openAgentModelModal("agy");
    const modelSelect = app.sandbox.document.getElementById("model-select-input");
    const effortSelect = app.sandbox.document.getElementById("effort-select-input");
    assert.equal(modelSelect.value, "gemini-3.6-flash");
    assert.match(modelSelect.innerHTML, /Gemini 3\.6 Flash/);
    assert.equal(modelSelect.innerHTML.includes("gemini-3.6-flash-low"), false);
    assert.match(effortSelect.innerHTML, /value="medium"/);
    assert.equal(effortSelect.innerHTML.includes('value="xhigh"'), false);

    modelSelect.value = "gemini-3.1-pro";
    modelSelect.listeners.change();
    assert.match(effortSelect.innerHTML, /value="low"/);
    assert.match(effortSelect.innerHTML, /value="high"/);
    assert.equal(effortSelect.innerHTML.includes('value="medium"'), false);
});

test("Apply dispatches the native model command without a confirmation dialog or composer changes", async () => {
    let confirmationCalls = 0;
    let confirmApplied = true;
    const app = loadFrontend({ confirm: () => { confirmationCalls += 1; throw new Error("window.confirm should not be called"); } });
    vm.runInContext(`
        activeRoomId = "room-model-apply";
        roomsList = [{ id: "room-model-apply", name: "voice_channel" }];
    `, app.sandbox);
    const commandInput = app.sandbox.document.getElementById("command-input");
    commandInput.value = "Keep this unrelated draft";
    const sendFeedback = app.sandbox.document.getElementById("send-feedback");
    sendFeedback.textContent = "Failed to send message: stale test error";
    sendFeedback.className = "send-feedback error";
    sendFeedback.style.display = "block";
    app.sandbox.document.getElementById("model-target-agent").value = "codex";
    app.sandbox.document.getElementById("model-select-input").value = "gpt-5.6-terra";
    app.sandbox.document.getElementById("effort-select-input").value = "high";

    const requests = [];
    const agents = {
        codex: { agent: "codex", available: true, current_model: "gpt-5.6-terra", current_effort: "high", display_model: "GPT 5.6 Terra", display_effort: "high", display_label: "Codex · GPT 5.6 Terra · high", available_models: ["gpt-5.6-sol", "gpt-5.6-terra"], available_efforts: ["low", "high"] },
        agy: { agent: "agy", available: true, current_model: "Gemini 3.5 Flash (High)", current_effort: "low", display_model: "Gemini 3.5 Flash (High)", display_effort: "low", available_models: ["Gemini 3.5 Flash (High)"], available_efforts: ["low"] },
        claude: { agent: "claude", available: true, current_model: "claude-opus-5", current_effort: "high", display_model: "Claude Opus 5", display_effort: "high", available_models: ["claude-opus-5"], available_efforts: ["high"] },
        grok: { agent: "grok", available: true, current_model: "grok-4.5", current_effort: "high", display_model: "Grok 4.5", display_effort: "high", available_models: ["grok-4.5"], available_efforts: ["high"] }
    };
    app.sandbox.fetch = (url, options = {}) => {
        requests.push({ url, options });
        if (url === "/api/agent-models/prepare") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    confirmation_message: "Change Codex in #voice_channel?",
                    confirmation_snapshot: {
                        schema_version: "1.0",
                        immutable_interaction_id: "int-model-ui",
                        room_id: "room-model-apply",
                        agent: "codex",
                        exact_message: "!model codex gpt-5.6-terra high",
                        permission_tier: "commit",
                        expires_at: 9999999999,
                        nonce: "nonce-model-ui"
                    },
                    change: {
                        formatted_command: "!model codex gpt-5.6-terra high",
                        target_model: "gpt-5.6-terra",
                        target_effort: "high"
                    }
                })
            });
        }
        if (url === "/api/agent-models/confirm") {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    status: "applied",
                    applied: confirmApplied,
                    effective: { current_model: "gpt-5.6-terra", current_effort: "high" },
                    rocket_chat_msg_ids: ["rc-model-ui"]
                })
            });
        }
        if (url.startsWith("/api/agent-models?")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({ success: true, room_id: "room-model-apply", channel_name: "voice_channel", agents })
            });
        }
        throw new Error("Unexpected request: " + url);
    };

    await app.sandbox.applyAgentModelSwitch(false);

    assert.equal(confirmationCalls, 0);
    assert.equal(commandInput.value, "Keep this unrelated draft");
    assert.equal(requests.some(request => request.url === "/api/gateway/interact"), false);
    const confirmRequest = requests.find(request => request.url === "/api/agent-models/confirm");
    assert.ok(confirmRequest);
    const confirmBody = JSON.parse(confirmRequest.options.body);
    assert.equal(confirmBody.confirmation_snapshot.exact_message, "!model codex gpt-5.6-terra high");
    assert.equal(
        app.sandbox.document.getElementById("model-control-status").innerText,
        "Applied: GPT 5.6 Terra · high."
    );
    assert.equal(sendFeedback.style.display, "none");
    assert.equal(sendFeedback.textContent, "");
    assert.equal(sendFeedback.className, "send-feedback hidden");

    // A successful Rocket.Chat post can briefly precede ACLI's persisted
    // state becoming visible. That is pending, not a model-switch error.
    confirmApplied = false;
    await app.sandbox.applyAgentModelSwitch(false);
    const pendingStatus = app.sandbox.document.getElementById("model-control-status");
    assert.equal(pendingStatus.className, "model-control-status pending");
    assert.equal(
        pendingStatus.innerText,
        "The model command was sent to Rocket.Chat; ACLI is still finalizing the persisted change."
    );
    assert.equal(sendFeedback.style.display, "none");
    assert.equal(sendFeedback.textContent, "");
    assert.equal(sendFeedback.className, "send-feedback hidden");
});

test("agent responses render read-aloud beside copy while user messages remain silent", () => {
    const app = loadFrontend();
    const messages = [
        {
            id: "msg-copy-test-1",
            name: "Codex",
            username: "codex",
            text: "Hello, this is a test message to copy & paste!",
            timestamp: "2026-08-05T18:00:00.000Z",
            lane: "agent",
            event: { kind: "agent_response", agent: "codex" }
        },
        {
            id: "msg-user-test-1",
            name: "Ed",
            username: "ed",
            text: "This user message should not get a speech button.",
            timestamp: "2026-08-05T18:01:00.000Z",
            lane: "user",
            event: { kind: "user_message" }
        }
    ];

    app.sandbox.renderTranscript(messages);
    const feed = app.sandbox.document.getElementById("transcript-feed");
    assert.ok(feed.innerHTML.includes('class="btn-copy-msg"'));
    assert.ok(feed.innerHTML.includes('data-raw-text="Hello, this is a test message to copy &amp; paste!"'));
    assert.ok(feed.innerHTML.includes('content_copy'));
    assert.equal((feed.innerHTML.match(/class="btn-read-msg/g) || []).length, 1);
    assert.ok(feed.innerHTML.includes('data-message-id="msg-copy-test-1"'));
    assert.ok(feed.innerHTML.includes('data-author="Codex"'));
    assert.ok(feed.innerHTML.includes('volume_up'));
});

test("message read-aloud keeps one active response and exposes pause stop and close", () => {
    const app = loadFrontend({ enableSpeech: true });
    const synth = app.sandbox.window.speechSynthesis;
    let cancelCount = 0;
    let pauseCount = 0;
    let resumeCount = 0;
    synth.cancel = () => { cancelCount += 1; };
    synth.pause = () => { pauseCount += 1; synth.paused = true; };
    synth.resume = () => { resumeCount += 1; synth.paused = false; };
    synth.paused = false;

    app.sandbox.startMessageReadAloud({
        messageId: "agent-grok-1",
        roomId: "room-daily",
        author: "Grok",
        text: "The first worker response."
    });

    const player = app.sandbox.document.getElementById("message-read-player");
    const title = app.sandbox.document.getElementById("message-read-player-title");
    const status = app.sandbox.document.getElementById("message-read-player-status");
    const toggleIcon = app.sandbox.document.getElementById("message-read-toggle-icon");
    assert.equal(player.hidden, false);
    assert.equal(title.innerText, "Grok response");
    assert.equal(status.innerText, "Playing · Fast voice");
    assert.deepEqual(app.spoken, ["The first worker response."]);

    // Starting another worker response replaces the active reading in place.
    app.sandbox.startMessageReadAloud({
        messageId: "agent-codex-2",
        roomId: "room-daily",
        author: "Codex",
        text: "The replacement response."
    });
    assert.equal(title.innerText, "Codex response");
    assert.deepEqual(app.spoken, ["The first worker response.", "The replacement response."]);
    assert.ok(cancelCount >= 2);
    assert.equal(vm.runInContext("messageReadAloudState.messageId", app.sandbox), "agent-codex-2");

    synth.speaking = true;
    app.sandbox.toggleMessageReadAloud();
    assert.equal(pauseCount, 1);
    assert.equal(status.innerText, "Paused · Fast voice");
    assert.equal(toggleIcon.textContent, "play_arrow");

    app.sandbox.toggleMessageReadAloud();
    assert.equal(resumeCount, 1);
    assert.equal(status.innerText, "Playing · Fast voice");
    assert.equal(toggleIcon.textContent, "pause");

    app.sandbox.stopMessageReadAloud();
    assert.equal(player.hidden, false);
    assert.equal(status.innerText, "Stopped · Fast voice");
    assert.equal(toggleIcon.textContent, "play_arrow");

    app.sandbox.closeMessageReadAloud();
    assert.equal(player.hidden, true);
    assert.equal(vm.runInContext("messageReadAloudState", app.sandbox), null);
});

test("push-to-talk speech recognition appends transcript, updates draft state, and requires explicit send", async () => {
    const app = loadFrontend();
    const commandInput = app.sandbox.document.getElementById("command-input");
    const btnMic = app.sandbox.document.getElementById("command-input") ? app.sandbox.document.getElementById("btn-mic") : null;

    vm.runInContext(`
        activeRoomId = "room-stt-test";
        roomsList = [{ id: "room-stt-test", name: "voice_channel" }];
    `, app.sandbox);

    // Initial state
    commandInput.value = "@codex initial prompt";
    app.sandbox.syncCommandDraftState(commandInput.value);

    let recognitionStarted = false;
    let recognitionStopped = false;

    // Mock Web Speech API SpeechRecognition
    class MockSpeechRecognition {
        constructor() {
            this.continuous = false;
            this.interimResults = false;
            this.lang = "en-US";
        }
        start() {
            recognitionStarted = true;
            if (this.onstart) this.onstart();
        }
        stop() {
            recognitionStopped = true;
            if (this.onend) this.onend();
        }
    }

    app.sandbox.window.SpeechRecognition = MockSpeechRecognition;
    app.sandbox.initSpeechRecognition();

    // Toggle speech input on
    app.sandbox.toggleSpeechInput();
    assert.equal(recognitionStarted, true);
    assert.equal(vm.runInContext("isListening", app.sandbox), true);
    assert.ok(btnMic.classList.contains("btn-danger"));

    // Simulate STT result event
    const recognition = vm.runInContext("recognition", app.sandbox);
    recognition.onresult({
        results: [[{ transcript: "check task status and report back" }]]
    });

    // Verify text appended, focused, and draft synced
    assert.equal(commandInput.value, "@codex initial prompt check task status and report back");
    assert.equal(app.sandbox.getRoomState("room-stt-test").draftText, "@codex initial prompt check task status and report back");
    assert.equal(app.storage["vc_draft_room-stt-test"], "@codex initial prompt check task status and report back");

    // Speech input stops
    app.sandbox.toggleSpeechInput();
    assert.equal(recognitionStopped, true);
    assert.equal(vm.runInContext("isListening", app.sandbox), false);
    assert.ok(btnMic.classList.contains("btn-secondary"));

    // Crucial safety check: STT transcription does NOT automatically send the message!
    assert.equal(app.sandbox.getRoomState("room-stt-test").draftText, "@codex initial prompt check task status and report back");

    // Ed can edit text before sending
    commandInput.value = "@codex check task status";
    app.sandbox.syncCommandDraftState(commandInput.value);

    // Explicit send confirmation
    let interactPayload = null;
    let confirmPayload = null;
    app.sandbox.fetch = async (url, opts) => {
        if (url === "/api/gateway/interact") {
            interactPayload = JSON.parse(opts.body);
            return {
                ok: true,
                json: async () => ({
                    success: true,
                    confirmation_snapshot: {
                        immutable_interaction_id: "int_stt_001",
                        room_id: "room-stt-test",
                        agent: "codex",
                        exact_message: "@codex check task status",
                        nonce: "nonce_stt_1"
                    }
                })
            };
        }
        if (url === "/api/gateway/confirm") {
            confirmPayload = JSON.parse(opts.body);
            return {
                ok: true,
                json: async () => ({
                    status: "posted",
                    rocket_chat_msg_ids: ["msg_stt_posted_1001"]
                })
            };
        }
        if (url.startsWith("/api/history")) {
            return { ok: true, json: async () => ({ success: true, messages: [] }) };
        }
        return { ok: true, json: async () => ({ success: true }) };
    };

    await app.sandbox.sendMessage();
    assert.equal(interactPayload.raw_input, "@codex check task status");
    assert.equal(confirmPayload.exact_message, "@codex check task status");
    assert.equal(commandInput.value, "");
});
test("history poll is suppressed only for explicitly covered rooms with fresh watermarks and active SSE", async () => {
    const app = loadFrontend();
    app.documentListeners.DOMContentLoaded();
    let historyCalls = 0;
    app.sandbox.loadHistory = async () => { historyCalls++; };
    vm.runInContext("activeRoomId = 'room-test';", app.sandbox);

    const historyInterval = app.intervals.find(i => i.delay === 4000);
    assert.ok(historyInterval, "4s history interval should exist");

    // Case 1: SSE active, but no watermark -> unsuppressed (uncovered)
    app.sandbox.window.sseConnected = true;
    app.sandbox.window.webhookCoveredRooms = {};
    historyInterval.callback();
    assert.equal(historyCalls, 1, "Should poll uncovered room");

    // Case 2: SSE active, watermark exists but stale (> 120s) -> unsuppressed
    app.sandbox.window.webhookCoveredRooms = { "room-test": (Date.now() / 1000) - 150 };
    historyInterval.callback();
    assert.equal(historyCalls, 2, "Should poll stale room");

    // Case 3: SSE active, watermark exists and fresh -> suppressed (covered)
    app.sandbox.window.webhookCoveredRooms = { "room-test": (Date.now() / 1000) - 10 };
    historyInterval.callback();
    assert.equal(historyCalls, 2, "Should suppress covered room");

    // Case 4: Watermark fresh, but SSE disconnected -> unsuppressed
    app.sandbox.window.sseConnected = false;
    historyInterval.callback();
    assert.equal(historyCalls, 3, "Should poll when SSE disconnected despite coverage");
});

test("prewarm_ready consumes cached result without double generation", async (t) => {
    const app = loadFrontend();

    // Mock EventSource
    let eventListeners = {};
    app.sandbox.EventSource = class {
        constructor(url) { this.url = url; }
        addEventListener(event, callback) { eventListeners[event] = callback; }
    };

    app.documentListeners.DOMContentLoaded();

    // Mock loadHistory and handleGenerateDigest to observe which is called
    let historyCalls = 0;
    let digestCalls = 0;
    app.sandbox.loadHistory = async () => { historyCalls++; };
    app.sandbox.handleGenerateDigest = async () => { digestCalls++; };
    app.sandbox.fetchAttentionQueue = async () => {};

    vm.runInContext("activeRoomId = 'room-test';", app.sandbox);
    vm.runInContext("initSSEEventSource();", app.sandbox);

    // Emit prewarm_ready
    const prewarmReadyCb = eventListeners["prewarm_ready"];
    assert.ok(prewarmReadyCb, "prewarm_ready listener should be registered");

    prewarmReadyCb({ data: JSON.stringify({ room_id: "room-test" }) });

    assert.equal(historyCalls, 1, "loadHistory should be called");
    assert.equal(digestCalls, 0, "handleGenerateDigest should NOT be called");
});
