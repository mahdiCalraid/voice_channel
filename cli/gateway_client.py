"""CLI Fixture Client Adapter for Voice Channel Gateway.

Submits text interactions to the Gateway via InteractionRequest,
inspects returned ConfirmationSnapshot, confirms execution,
and outputs the resulting Rocket.Chat message ID.
"""

import sys
import argparse
import httpx
from typing import Optional

def run_gateway_client(
    gateway_url: str,
    raw_input: str,
    requested_room_id: Optional[str] = None,
    requested_agent: Optional[str] = None,
    auto_confirm: bool = True
) -> str:
    interact_url = f"{gateway_url.rstrip('/')}/api/gateway/interact"
    confirm_url = f"{gateway_url.rstrip('/')}/api/gateway/confirm"

    payload = {
        "schema_version": "1.0",
        "raw_input": raw_input,
        "input_mode": "text",
        "client_id": "cli_client"
    }
    if requested_room_id:
        payload["requested_room_id"] = requested_room_id
    if requested_agent:
        payload["requested_agent"] = requested_agent

    # Step 1: Submit Interaction Request
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(interact_url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Gateway interact error ({resp.status_code}): {resp.text}")
        
        data = resp.json()
        status = data.get("status")
        metadata = data.get("provider_metadata", {})
        conf_snapshot = metadata.get("confirmation_snapshot")

        if not conf_snapshot:
            raise RuntimeError(f"Gateway did not return a confirmation snapshot. Payload: {data}")

        if not auto_confirm:
            print(f"Interaction prepared. Status: {status}")
            print(f"Confirmation Snapshot: {conf_snapshot}")
            return conf_snapshot.get("immutable_interaction_id")

        # Step 2: Submit Confirmation Snapshot
        confirm_resp = client.post(confirm_url, json=conf_snapshot)
        if confirm_resp.status_code != 200:
            raise RuntimeError(f"Gateway confirm error ({confirm_resp.status_code}): {confirm_resp.text}")

        confirm_data = confirm_resp.json()
        msg_ids = confirm_data.get("rocket_chat_msg_ids", [])
        msg_id = msg_ids[0] if msg_ids else "unknown"
        return msg_id


def main():
    parser = argparse.ArgumentParser(description="Voice Channel CLI Client Adapter")
    parser.add_argument("--gateway-url", default="http://localhost:6891", help="Base URL of gateway API")
    parser.add_argument("--text", required=True, help="Raw text interaction prompt")
    parser.add_argument("--room-id", help="Target Rocket.Chat room ID")
    parser.add_argument("--agent", help="Target agent name")
    parser.add_argument("--no-confirm", action="store_true", help="Stop after interaction request draft")

    args = parser.parse_args()

    try:
        msg_id = run_gateway_client(
            gateway_url=args.gateway_url,
            raw_input=args.text,
            requested_room_id=args.room_id,
            requested_agent=args.agent,
            auto_confirm=not args.no_confirm
        )
        print(f"SUCCESS: Message ID = {msg_id}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
