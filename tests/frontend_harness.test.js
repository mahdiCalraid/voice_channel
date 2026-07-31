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
        toggle: item => values.has(item) ? values.delete(item) : values.add(item),
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
        setInterval: () => 0,
        setTimeout: callback => { callback(); return 0; },
        window: {
            VoiceChannelHistoryState: historyState,
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
    return { elements, sandbox, spoken, storage, storageWrites, documentListeners };
}

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
    assert.equal(settings.settingsVersion, 3);
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
    assert.equal(persisted.settingsVersion, 3);
    assert.equal(persisted.systemFontSize, "xlarge");
    assert.equal(persisted.chatFontFamily, "georgia");
    assert.equal(persisted.chatFontSize, 24);
    assert.equal(persisted.chatLineHeight, 1.9);
    assert.equal(bodyStyle.getPropertyValue("--system-font-size"), "35px");
});

test("confirmation gate locks composer text and room switch clears confirmation", () => {
    const app = loadFrontend();
    const input = app.sandbox.document.getElementById("command-input");
    const gate = app.sandbox.document.getElementById("confirmation-gate");
    const btnPre = app.sandbox.document.getElementById("btn-pre-send");

    input.value = "Test confirmation message";
    vm.runInContext('activeRoomId = "room-1";', app.sandbox);
    vm.runInContext('showConfirmation();', app.sandbox);

    assert.equal(input.readOnly, true);
    assert.equal(gate.classList.contains("hidden"), false);
    assert.equal(btnPre.classList.contains("hidden"), true);
    assert.equal(vm.runInContext('confirmationTargetRoomId', app.sandbox), "room-1");

    // Switch rooms -> confirmation must be cleared and unlocked
    vm.runInContext('selectRoom("room-2");', app.sandbox);
    assert.equal(input.readOnly, false);
    assert.equal(gate.classList.contains("hidden"), true);
    assert.equal(vm.runInContext('confirmationTargetRoomId', app.sandbox), null);
});

test("browser composer prepares and confirms through gateway contracts", async () => {
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

    await app.sandbox.showConfirmation();
    assert.equal(requests[0].url, "/api/gateway/interact");
    assert.equal(app.sandbox.document.getElementById("btn-confirm-send").disabled, false);

    await app.sandbox.sendDraftedMessage();
    assert.equal(requests[1].url, "/api/gateway/confirm");
    assert.equal(JSON.parse(requests[1].options.body).nonce, "nonce-browser");
    assert.equal(input.value, "");
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
