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
    const document = {
        readyState: "loading",
        addEventListener: () => {},
        createElement: () => element(),
        getElementById: id => {
            if (!elements.has(id)) elements.set(id, element());
            return elements.get(id);
        },
        querySelector: selector => element(),
        querySelectorAll: () => [],
    };
    const storage = {};
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
            setItem: (key, val) => { storage[key] = val; },
            removeItem: key => { delete storage[key]; }
        },
        setInterval: () => 0,
        setTimeout: callback => { callback(); return 0; },
        window: {
            VoiceChannelHistoryState: historyState,
            speechSynthesis: { cancel: () => {}, getVoices: () => [], speaking: false },
        },
    };
    vm.createContext(sandbox);
    const source = fs.readFileSync(path.join(__dirname, "..", "frontend", "index.js"), "utf8");
    vm.runInContext(source, sandbox);
    return { elements, sandbox, storage };
}

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
