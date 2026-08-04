# U-08 Chatterbox feasibility trial

Date: 2026-08-03
Branch: `urgent/daily-use-console`
Status: **in progress — Ed's subjective quality decision remains**

## Selection and constraints

- Mac: Apple M2 Max, 32 GB RAM, macOS 26.2.
- Disk headroom before the trial: approximately 17 GiB.
- Official Chatterbox choice: **Chatterbox-Turbo**, the English 350M model intended
  for lower-latency use. The official project documents Python 3.11 and `mps`/CPU
  device support. The base model repository is MIT licensed and approximately
  4.04 GB for the current PyTorch checkpoint set.
- Trial runtime: Python 3.11.6, `mlx-audio==0.4.7`, MLX on Apple Silicon.
- Trial checkpoint: `mlx-community/chatterbox-turbo-8bit`, a 706 MB MLX conversion
  of the Resemble AI Turbo model. The conversion repository is Apache-2.0; it is
  not the official Resemble AI distribution.
- No reference audio or voice cloning was used.

Sources: [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox),
[official Turbo model](https://huggingface.co/ResembleAI/chatterbox-turbo), and
[MLX 8-bit conversion](https://huggingface.co/mlx-community/chatterbox-turbo-8bit).

## Repeatable local trial

The model was run through MLX-Audio's API server bound to `127.0.0.1:8765`; no LAN
or public listener was opened. `GET /` returned HTTP 200, and `POST /v1/audio/speech`
returned valid 24 kHz mono WAV audio. The service was stopped after the measurements.

The Python environment, model cache, and generated audio lived under `/tmp` only;
nothing was written to the repository, ACLI runtime folders, or logs. The fixture text
was not retained in this report.

## Measurements

| Measurement | Result |
| --- | ---: |
| Cold request, including first model fetch/load | 40.72 s |
| Warm request | 2.64 s |
| Warm process physical footprint | 1.3 GiB |
| Peak physical footprint during load | 4.7 GiB |
| Isolated model cache after download | 1.1 GiB |
| Cold output | 8.04 s, 377 KiB WAV |
| Warm output | 4.56 s, 214 KiB WAV |
| macOS `say` comparison generation | 1.16 s |

The Chatterbox output generated successfully and was played locally. A meaningful
quality comparison against the current browser/macOS voice still requires Ed's
subjective listen-and-choose decision; this automated trial cannot establish that.

## Digest-length latency measurement (2026-08-04)

The first trial used a short fixture, which understates the latency a real narrator
digest would show. This run used a 132-word digest paragraph (46 s of speech), the
realistic length, on a warm loopback service. Script and audio:
`scratch/tts_ab/` (untracked; audio is disposable).

| Variant | Time to first audio | Total synthesis | Audio length |
| --- | ---: | ---: | ---: |
| Chatterbox, whole paragraph in one request | **17.22 s** | 17.22 s | 46.03 s |
| Chatterbox, sentence-chunked (6 chunks) | **1.76 s** | 12.83 s | 45.66 s |
| macOS `say` / browser Web Speech | **< 1 s** | n/a (streams) | ~46 s |

Real-time factor for the whole-paragraph run was 0.374, better than the 0.58 implied
by the short fixture. The decisive result is the chunked run: every chunk after the
first synthesized in less time than the previous chunk's playback duration
(worst case 5.18 s of synthesis behind 7.76 s of playback), so streaming playback
never starves and the perceived wait is the first sentence only.

Warm-service resident memory measured 0.17 GiB between requests in this run; the
earlier 1.3 GiB figure was taken during active synthesis. Boot volume free space is
now 17 GiB with the model cache held under `/tmp`.

**Design consequence:** whole-paragraph synthesis is not viable — 17 s of silence
after pressing Play would be worse than today's voice regardless of timbre. If U-09
proceeds it must synthesize sentence by sentence and start playback on the first
chunk, and fall back to browser speech on latency, not only on error.

## Current gate decision

**Technical feasibility: pass.** The local service starts, stays loopback-only, loads
one model, passes a health check, synthesizes audio, and has a usable warm latency.

**U-09 decision: pending quality gate.** Do not integrate Chatterbox or change the
default narrator until Ed confirms that its voice quality is materially better for
editable narrator text. Browser Web Speech remains the daily-use path.
