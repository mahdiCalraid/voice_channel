const assert = require("node:assert/strict");
const test = require("node:test");
const historyState = require("../frontend/history_state.js");

function message(id, timestamp) {
    return { id, timestamp, text: id };
}

test("older pages deduplicate their inclusive boundary and remain chronological", () => {
    const state = historyState.createRoomState();
    historyState.applyLatestPage(state, [
        message("m3", "2026-07-23T12:03:00.000Z"),
        message("m4", "2026-07-23T12:04:00.000Z"),
    ], true);

    const result = historyState.applyOlderPage(state, [
        message("m1", "2026-07-23T12:01:00.000Z"),
        message("m2", "2026-07-23T12:02:00.000Z"),
        message("m3", "2026-07-23T12:03:00.000Z"),
    ], true, "2026-07-23T12:01:00.000Z");

    assert.equal(result.added, 2);
    assert.deepEqual(state.orderedIds, ["m1", "m2", "m3", "m4"]);
    assert.equal(state.oldestCursor, "2026-07-23T12:01:00.000Z");
    assert.equal(state.hasMoreOlder, true);
});

test("live refresh retains older messages and an exhausted older page stops paging", () => {
    const state = historyState.createRoomState();
    historyState.applyLatestPage(state, [
        message("m2", "2026-07-23T12:02:00.000Z"),
        message("m3", "2026-07-23T12:03:00.000Z"),
    ], true);
    historyState.applyOlderPage(state, [
        message("m1", "2026-07-23T12:01:00.000Z"),
        message("m2", "2026-07-23T12:02:00.000Z"),
    ], true, "2026-07-23T12:01:00.000Z");
    historyState.applyLatestPage(state, [
        message("m3", "2026-07-23T12:03:00.000Z"),
        message("m4", "2026-07-23T12:04:00.000Z"),
    ], true);

    const exhausted = historyState.applyOlderPage(state, [
        message("m1", "2026-07-23T12:01:00.000Z"),
    ], false, "2026-07-23T12:01:00.000Z");

    assert.deepEqual(state.orderedIds, ["m1", "m2", "m3", "m4"]);
    assert.equal(exhausted.added, 0);
    assert.equal(state.hasMoreOlder, false);
});

test("prepend scroll restoration keeps the reader anchored", () => {
    assert.equal(historyState.restoredPrependScrollTop(1000, 350, 1450), 800);
    assert.equal(historyState.restoredPrependScrollTop(1000, 350, 900), 350);
});
