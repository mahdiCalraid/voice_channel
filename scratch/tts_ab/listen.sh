#!/bin/bash
# Play the U-08 A/B set back to back, same 132-word digest in each.
# Judge timbre only; the latency numbers are in out/results.json.
cd "$(dirname "$0")/out" || exit 1
for f in chatterbox_chunked.wav macos_say_ava.aiff macos_say_zoe.aiff macos_say_samantha.aiff; do
    echo ">>> $f"
    afplay "$f"
    sleep 1
done
