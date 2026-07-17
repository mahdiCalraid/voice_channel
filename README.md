# Voice Channel Console

An audio-first console and command center for Rocket.Chat channels. It acts as a selective narrator and supervisor interface over agent workspaces.

## Features
- **Live Narrator (TTS)**: Fetches recent Rocket.Chat messages, formats them to remove system/markup noise, and summarizes them into an audio digest read aloud using standard browser speech synthesis.
- **Voice Dictation (STT)**: Allows voice commands and speech-to-text drafting.
- **Agent Command Hub**: Shortcuts to target specific agents (`@codex`, `@gemini`, `@claude`, `@grok`) with preset or custom commands.
- **Confirmation Gate**: Any message or command drafted is held in a confirmation container and is only transmitted to Rocket.Chat once manually/verbally approved.
- **Premium Glassmorphic UI**: Sleek dark interface with animated visualizers and pulsing status chips.

## Directory Structure
```text
/Users/ed/King/clawd_2/voice_channel
├── app/
│   └── main.py          # FastAPI backend server
├── frontend/
│   ├── index.html       # Console user interface
│   ├── index.css        # Premium styling (glassmorphism/dark mode)
│   └── index.js         # Audio engines (TTS/STT) & API wiring
├── Dockerfile           # Builds python service container
├── docker-compose.yml   # Maps port 6891 & configures host networking
├── restart.sh           # Executable script to rebuild & restart console
├── requirements.txt     # Python backend dependencies
└── README.md            # System documentation
```

## Getting Started

### 1. Environment Configuration
The backend retrieves the required environment variables directly from the host system. It uses the following variables (automatically resolved from your shell):
- `ACLI_RC_URL`: Base Rocket.Chat URL (e.g. `http://localhost:3000`). Inside the Docker container, it automatically maps `localhost` to `host.docker.internal` to bridge to the host machine.
- `ACLI_RC_USER_ID` / `ACLI_RC_AUTH_TOKEN`: Credentials for `acli_bot` to read history and post messages.
- `OPENAI_API_KEY`: API key for generating AI digests. If the key is invalid or absent, the console automatically falls back to an elegant rule-based summary generator.

### 2. Launching or Restarting the Server
To start or update the server after any code modification, run the restart helper script from the root workspace:

```bash
./restart.sh
```

This script:
1. Stops any running instances of the console.
2. Rebuilds the Docker image to include any backend or frontend updates.
3. Launches the container in the background, listening on **port 6891**.
4. Provides a link to access the app and instructions for viewing logs.

### 3. Monitoring Server Logs
To watch the backend application logs in real-time, run:

```bash
docker-compose logs -f
```

## API Endpoints
- `GET /api/status`: Returns status of backend server, OpenAI client, and the connection status to Rocket.Chat.
- `GET /api/history?count=N`: Fetches the last `N` messages from `#voice_channel`, filtering out system messages and formatting text.
- `POST /api/digest`: Generates an audio-optimized digest of the messages (Markdown-free, concise narration) utilizing OpenAI or rule-based fallback.
- `POST /api/send`: Posts a drafted message/command to `#voice_channel`.
