// Room-local transcript state shared by the browser and deterministic tests.
(function exposeHistoryState(root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.VoiceChannelHistoryState = api;
})(typeof window !== "undefined" ? window : globalThis, function createHistoryStateApi() {
    function createRoomState() {
        return {
            messageMap: {},
            orderedIds: [],
            oldestCursor: null,
            newestTimestamp: null,
            hasMoreOlder: true,
            loadingOlder: false,
            pendingLatest: null,
        };
    }

    function compareMessages(left, right) {
        const byTimestamp = String(left.timestamp || "").localeCompare(String(right.timestamp || ""));
        return byTimestamp || String(left.id || "").localeCompare(String(right.id || ""));
    }

    function updateBounds(state) {
        if (state.orderedIds.length === 0) {
            state.oldestCursor = null;
            state.newestTimestamp = null;
            return;
        }

        state.orderedIds.sort((leftId, rightId) => {
            return compareMessages(state.messageMap[leftId], state.messageMap[rightId]);
        });
        state.oldestCursor = state.messageMap[state.orderedIds[0]].timestamp;
        state.newestTimestamp = state.messageMap[state.orderedIds[state.orderedIds.length - 1]].timestamp;
    }

    function mergeMessages(state, messages) {
        const previousOldestCursor = state.oldestCursor;
        let added = 0;
        let updated = 0;

        for (const message of messages || []) {
            if (!message || !message.id) continue;
            if (state.messageMap[message.id]) {
                updated += 1;
            } else {
                state.orderedIds.push(message.id);
                added += 1;
            }
            state.messageMap[message.id] = message;
        }

        updateBounds(state);
        return {
            added,
            updated,
            oldestChanged: previousOldestCursor !== state.oldestCursor,
        };
    }

    function applyLatestPage(state, messages, hasMore) {
        const wasEmpty = state.orderedIds.length === 0;
        const result = mergeMessages(state, messages);
        if (wasEmpty) {
            state.hasMoreOlder = Boolean(hasMore);
        }
        return result;
    }

    function applyOlderPage(state, messages, hasMore, nextBefore) {
        const result = mergeMessages(state, messages);
        if (nextBefore && result.added > 0) {
            state.oldestCursor = nextBefore;
        }
        // A page containing only the inclusive boundary cannot make progress.
        state.hasMoreOlder = Boolean(hasMore) && result.added > 0;
        return result;
    }

    function restoredPrependScrollTop(oldScrollHeight, oldScrollTop, newScrollHeight) {
        return oldScrollTop + Math.max(0, newScrollHeight - oldScrollHeight);
    }

    return {
        createRoomState,
        mergeMessages,
        applyLatestPage,
        applyOlderPage,
        restoredPrependScrollTop,
    };
});
