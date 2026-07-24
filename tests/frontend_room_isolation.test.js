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
        localStorage: { getItem: () => null, setItem: () => {} },
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
