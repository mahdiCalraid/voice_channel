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
    return {
        classList: classList(),
        dataset: {},
        disabled: false,
        innerHTML: "",
        innerText: "",
        offsetWidth: 1,
        readOnly: false,
        scrollTop: 0,
        style: { display: "none" },
        value: "",
        listeners,
        addEventListener: (name, handler) => { listeners[name] = handler; },
        focus: () => {},
        querySelector: () => element(),
        querySelectorAll: () => [],
        scrollIntoView: () => {},
    };
}

function loadFrontend() {
    const elements = new Map();
    const sourcesList = element();
    elements.set("sources-list", sourcesList);
    const targetMessage = element();
    let queriedMessageId = null;

    const document = {
        readyState: "loading",
        addEventListener: () => {},
        createElement: () => element(),
        getElementById: id => {
            if (!elements.has(id)) elements.set(id, element());
            return elements.get(id);
        },
        querySelector: selector => {
            queriedMessageId = selector;
            return targetMessage;
        },
        querySelectorAll: () => [],
    };
    const sandbox = {
        console,
        document,
        fetch: () => Promise.resolve({ ok: true, json: async () => ({ success: true, messages: [] }) }),
        localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
        setInterval: () => 0,
        window: {
            VoiceChannelHistoryState: historyState,
            speechSynthesis: { cancel: () => {}, getVoices: () => [], speaking: false },
        },
    };
    vm.createContext(sandbox);
    const source = fs.readFileSync(path.join(__dirname, "..", "frontend", "index.js"), "utf8");
    vm.runInContext(source, sandbox);
    return { elements, queriedMessageId: () => queriedMessageId, sandbox, sourcesList, targetMessage };
}

test("restored source items rebind their transcript navigation handler", () => {
    const app = loadFrontend();
    const sourceItem = element();
    sourceItem.dataset.sourceId = "message-42";
    app.sourcesList.querySelectorAll = selector => selector === ".source-item" ? [sourceItem] : [];

    vm.runInContext(`
        const state = getRoomState("room-a");
        state.digestSourcesHtml = "<div class='source-item' data-source-id='message-42'></div>";
        state.digestSourcesVisible = true;
        state.sourcesListVisible = false;
        restoreRoomUIData("room-a");
    `, app.sandbox);

    assert.equal(typeof sourceItem.listeners.click, "function");
    sourceItem.listeners.click();
    assert.equal(app.queriedMessageId(), '[data-id="message-42"]');
    assert.equal(app.targetMessage.classList.contains("highlight-pulse"), true);
});

test("a digest abandoned by a room switch releases the originating room", async () => {
    const app = loadFrontend();
    vm.runInContext('activeRoomId = "room-a";', app.sandbox);

    const digestPromise = app.sandbox.handleGenerateDigest();
    vm.runInContext('activeRoomId = "room-b";', app.sandbox);
    await digestPromise;

    assert.equal(vm.runInContext('getRoomState("room-a").digestLoading', app.sandbox), false);
});

test("response assistance finishing after a room switch updates only its originating room", async () => {
    const app = loadFrontend();
    let resolveAssistant;
    const assistantPromise = new Promise(resolve => { resolveAssistant = resolve; });
    vm.runInContext(`
        activeRoomId = "room-a";
        roomsList = [
            { id: "room-a", name: "voice_channel" },
            { id: "room-b", name: "business.dev" }
        ];
    `, app.sandbox);

    app.sandbox.fetch = (url) => {
        if (url.startsWith("/api/history")) {
            return Promise.resolve({
                ok: true,
                json: async () => ({
                    success: true,
                    messages: [{
                        id: "agent-a",
                        lane: "agent",
                        username: "acli_bot",
                        name: "ACLI Bot",
                        text: "**@agy**: completed",
                        event: { kind: "agent_response", agent: "agy" }
                    }]
                })
            });
        }
        if (url === "/api/response-assistant") return assistantPromise;
        throw new Error("Unexpected request: " + url);
    };

    const generation = app.sandbox.handleGenerateDigest({
        autoPlay: false,
        triggerMessageId: "agent-a"
    });
    await new Promise(resolve => setImmediate(resolve));
    vm.runInContext('selectRoom("room-b");', app.sandbox);
    app.sandbox.document.getElementById("command-input").value = "@codex room B draft";

    resolveAssistant({
        ok: true,
        json: async () => ({
            digest: "AGY completed room A work.\n\nGrok should review room A.",
            phase: "review",
            suggested_message: "@grok Review room A.",
            trigger_message_id: "agent-a",
            included_message_ids: ["agent-a"]
        })
    });
    await generation;

    assert.equal(
        vm.runInContext('getRoomState("room-a").digestText', app.sandbox),
        "AGY completed room A work.\n\nGrok should review room A."
    );
    assert.equal(
        vm.runInContext('getRoomState("room-a").draftText', app.sandbox),
        "@grok Review room A."
    );
    assert.equal(
        app.sandbox.document.getElementById("command-input").value,
        "@codex room B draft"
    );
});

test("unsent draft is persisted to localStorage and restored for active room", () => {
    const storage = {};
    const app = loadFrontend();
    app.sandbox.localStorage = {
        getItem: key => storage[key] || null,
        setItem: (key, val) => { storage[key] = val; },
        removeItem: key => { delete storage[key]; }
    };
    
    vm.runInContext(`
        activeRoomId = "room-test";
        document.getElementById("command-input").value = "Drafting recovery message";
        saveRoomUIData("room-test");
    `, app.sandbox);
    
    assert.equal(storage["vc_draft_room-test"], "Drafting recovery message");
    
    // Simulate room restore after clearing volatile state
    vm.runInContext(`
        document.getElementById("command-input").value = "";
        getRoomState("room-test").draftText = null;
        restoreRoomUIData("room-test");
    `, app.sandbox);
    
    assert.equal(app.sandbox.document.getElementById("command-input").value, "Drafting recovery message");
});

test("network error during polling preserves existing rendered transcript DOM", async () => {
    const app = loadFrontend();
    const feed = app.sandbox.document.getElementById("transcript-feed");
    feed.innerHTML = "<div class='chat-msg'>Existing Message</div>";

    // Populate room state with a message
    vm.runInContext(`
        activeRoomId = "room-network-test";
        const state = getRoomState("room-network-test");
        state.orderedIds = ["msg-1"];
        state.messageMap["msg-1"] = { id: "msg-1", text: "Existing Message", lane: "user", timestamp: 1000 };
    `, app.sandbox);

    // Mock fetch to simulate a network error
    app.sandbox.fetch = () => Promise.reject(new Error("Network disconnect"));

    await app.sandbox.loadHistory();

    // Verify DOM was NOT replaced with signal_wifi_off error state
    assert.equal(feed.innerHTML.includes("signal_wifi_off"), false);
    assert.equal(feed.innerHTML.includes("Existing Message"), true);
});
