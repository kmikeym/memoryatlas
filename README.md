# MemoryAtlas

Voice memos to searchable, translated, enriched Obsidian notes. Local-first, privacy-preserving.

MemoryAtlas processes personal audio recordings through a four-stage pipeline:

```
Scan → Transcribe → Enrich → Polish
       (Whisper)    (LLM)    (LLM)
```

**Scan** imports metadata from Apple Voice Memos (or audio files).
**Transcribe** converts speech to text using Whisper (on-device, Apple Silicon optimized).
**Enrich** extracts summary, topics, people mentions, and sentiment via local LLM.
**Polish** restores source-language transcripts and produces literary English translations.

Each stage is independent and resumable. Output is Obsidian markdown notes with YAML frontmatter.

## Quick Start

```bash
# Clone
git clone https://github.com/peretzp/memoryatlas.git
cd memoryatlas

# Create venv (Python 3.11 required)
python3.11 -m venv .venv
source .venv/bin/activate

# Install
pip install -e .

# Configure (edit paths for your machine)
cp config.example.yaml config.yaml
# Edit config.yaml — see Configuration section below

# Initialize database and vault
atlas init

# Check health
atlas doctor
```

## Requirements

- **Python** 3.11 (tested; 3.12 works, 3.13 has compatibility issues)
- **ffmpeg** (for audio processing): `brew install ffmpeg` or `apt install ffmpeg`
- **Ollama** (for enrich + polish): https://ollama.com
- **Whisper** (for transcribe): `pip install mlx-whisper` (Apple Silicon) or `pip install openai-whisper` (CPU/CUDA)

### Platform Support

| Platform | Scan | Transcribe | Enrich | Polish |
|----------|------|------------|--------|--------|
| **macOS (Apple Silicon)** | Apple Voice Memos + audio files | mlx-whisper (fast, native) | Ollama | Ollama |
| **macOS (Intel)** | Apple Voice Memos + audio files | openai-whisper | Ollama | Ollama |
| **Linux (Anvil/GPU)** | Audio files only* | openai-whisper / faster-whisper | Ollama | Ollama |

*Linux cannot scan Apple Voice Memos directly. Transfer the audio files and import them, or sync the database via network mount.

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit for your machine:

```yaml
# Where MemoryAtlas stores its database and working files
data_dir: ~/tools/memoryatlas/data

# Apple Voice Memos database (macOS only — comment out on Linux)
apple_db_path: ~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/CloudRecordings.db

# Obsidian vault root (where notes are published)
vault_path: ~/Documents/PracticeLife
atlas_vault_dir: ~/Documents/PracticeLife/MemoryAtlas

# Ollama model for enrichment and philological polish
ollama_model: qwen2.5:32b
```

### Anvil / Linux Setup

```yaml
# config.yaml for Anvil (96GB M3 Ultra or GPU box)
data_dir: ~/memoryatlas/data

# No Apple Voice Memos on Linux — disable scan or point to audio directory
scan_voice_memos: false

# Vault can be a local directory (sync to Mac via git, rsync, or Syncthing)
vault_path: ~/obsidian-vault
atlas_vault_dir: ~/obsidian-vault/MemoryAtlas

# Use a larger model if you have the VRAM
ollama_model: llama3.3:70b
```

All paths support `~` expansion. Any field not specified falls back to the macOS default.

## Commands

### Core Pipeline

```bash
atlas scan                    # Import voice memo metadata into database
atlas transcribe              # Transcribe all pending memos (Whisper)
atlas enrich                  # Extract summary/topics/people/sentiment (Ollama)
atlas polish --language ru    # Restore source language + translate to English (Ollama)
```

### Options

```bash
# Limit batch size (useful for testing)
atlas transcribe --limit 5
atlas enrich --limit 10
atlas polish --limit 3

# Force a specific model
atlas transcribe --model mlx-community/whisper-large-v3-mlx
atlas enrich --model llama3.3:70b
atlas polish --model qwen2.5:32b

# Filter by language (polish only)
atlas polish --language ru    # Russian only
atlas polish --language ja    # Japanese only

# Preview without executing
atlas transcribe --dry-run
atlas enrich --dry-run
atlas polish --dry-run

# Verbose output
atlas transcribe --verbose
```

### LLM Backend Selection

```bash
# Default: local Ollama (private, no API costs)
atlas polish --backend ollama

# Claude API: literary-tier quality for crown jewels (requires ANTHROPIC_API_KEY)
atlas polish --backend claude --model claude-sonnet-4-5-20250929

# Auto: try Claude first, fall back to Ollama if unavailable
atlas polish --backend auto
```

### SRT Subtitle Processing

```bash
# Translate Russian subtitles to literary English
atlas polish-srt transcript.srt --language Russian --mode translate

# Restore source language (clean Whisper artifacts)
atlas polish-srt transcript.srt --language Russian --mode restore

# Both: restore then translate (outputs two files)
atlas polish-srt transcript.srt --language Russian --mode both

# Use Claude for highest quality
atlas polish-srt transcript.srt --backend claude
```

### Management

```bash
atlas init                    # Create database + vault directories
atlas status                  # Show statistics (total, hours, transcribed, enriched)
atlas doctor                  # Health check (dependencies, paths, database)
atlas health                  # Check Ollama server + model availability
atlas publish                 # (Re)generate Obsidian notes from database
atlas publish --force         # Regenerate all notes; notes edited by hand are left alone
atlas publish --index-only    # Only update the _Index.md file
atlas info <uuid>             # Show details for a single asset (prefix match OK)
```

## Testing

```bash
# Run all tests (55 tests, ~0.3s)
python -m pytest tests/ -v

# Run specific module
python -m pytest tests/test_philologist.py -v

# Tests use mocks — no Ollama or API keys needed
```

## Architecture

```
memoryatlas/
├── cli.py           # Typer CLI — all commands
├── config.py        # YAML config loader with defaults
├── constants.py     # Paths, versions, file extensions
├── db.py            # SQLite schema + AtlasDB class (WAL mode)
├── models.py        # Asset dataclass
├── scanner.py       # Apple Voice Memos importer
├── apple.py         # Apple database reader (read-only, immutable mode)
├── transcriber.py   # Whisper batch transcription
├── enricher.py      # Ollama metadata extraction
├── philologist.py   # Source restoration + literary translation + SRT
├── publisher.py     # Obsidian note generator
└── util.py          # Helpers (JSONL writer, formatters)

scripts/
└── deploy-to-anvil.sh  # Rsync code + setup venv on Anvil

tests/
├── conftest.py      # Shared fixtures (temp DB, config, assets)
├── test_config.py   # Config loading + YAML parsing
├── test_db.py       # Database operations
├── test_models.py   # Asset dataclass properties
├── test_philologist.py  # Pre-clean, SRT parse, LLM routing (mocked)
└── test_publisher.py    # Obsidian note generation
```

### Database

SQLite with WAL mode. Single `asset` table tracks each recording through the full pipeline:

```
asset
├── id, title, source_path, duration_sec, recorded_at
├── transcript_status, transcript_lang, transcript_path    ← Whisper
├── summary, topics, people, sentiment, enriched_at        ← Enricher
├── restored_text, translated_text, polished_at            ← Philologist
└── note_path, published_at, note_hash                     ← Publisher
```

Every operation is idempotent — reruns skip completed work. The `action_log` table provides a full audit trail.

### Data Directory

```
data/
├── atlas.db              # SQLite database
├── atlas.jsonl           # Append-only audit log
├── transcripts/          # Raw Whisper output (.txt per asset)
└── polished/             # Restored + translated texts
    ├── {id}_restored.txt
    └── {id}_translated.txt
```

## The Philological Pipeline

The `polish` command performs two-stage semantic enhancement:

**Stage 1 — Source Restoration**: Clean Whisper artifacts (hallucination spam, false credits, repetition loops), restore grammar and punctuation, add speaker attribution, preserve code-switching boundaries, flag genuinely unclear passages.

**Stage 2 — Literary Translation**: Produce register-matched English. A literary scholar discussing Chekhov gets erudite English; a grandmother describing her day gets warmth. Cultural concepts get bracketed glosses. Rhetorical force is preserved, not flattened.

This is not Google Translate. It is philology — the love of language applied to a personal archive.

### Pre-cleaning

Before the LLM sees a transcript, regex patterns strip mechanical Whisper artifacts:
- Hallucination spam ("stron stron stron" x200)
- False credit overlays ("Subtitle by DimaTorzok")
- Repetition loops ("To be continued..." x20)
- Excessive backchannel ("Uh-huh" x40 → keep 1-2)

### Obsidian Note Structure

Each published note contains up to four layers:

```markdown
---
title: Dad Call
recorded: 2025-06-15
duration: 122.9 min
language: ru
polished: true
---

## Summary            ← AI-generated metadata (enricher)
## Translation        ← Literary English (philologist stage 2)
## Restored Transcript ← Cleaned source language (philologist stage 1)
## Transcript         ← Link to raw Whisper output
```

## Resilience

The batch processing pipeline is built for unattended overnight runs:

- **Health check**: `atlas health` (or automatic pre-flight) verifies Ollama is up and model is loaded
- **Model preload**: Warms up the model before batch to avoid cold-start timeouts
- **Exponential backoff**: Retries transient failures (connection refused, timeouts) with 5s → 15s → 45s delays
- **Circuit breaker**: Stops batch after 5 consecutive failures (Ollama probably crashed)
- **Graceful interruption**: Ctrl+C finishes the current item, saves progress, then exits
- **Partial results saved**: If restoration succeeds but translation fails, the restored text is preserved

## Design Principles

1. **Local-first**: All processing on-device. No cloud APIs for sensitive voice data.
2. **Read-only source**: Apple Voice Memos database is opened in immutable mode. Never modified.
3. **Resumable**: Status tracked per-asset. Reruns skip completed work. Crash-safe.
4. **Privacy-preserving**: Ollama and Whisper run locally. Nothing leaves the machine.
5. **Idempotent**: Scan, publish, enrich, polish — all safe to run repeatedly.
6. **Platform-aware**: macOS paths are defaults, not requirements. Everything is configurable.

## Reusability

The pipeline shape — **capture → restore → translate → enrich** — applies to any source material:

- Voice memos in any language
- Documentary footage (SRT subtitle files)
- Historical documents (OCR'd manuscripts)
- Interview transcripts
- Meeting recordings
- Podcast episodes

The philological pipeline is language-agnostic. The restoration and translation prompts adapt to whatever language Whisper detected. Currently tested on Russian, Japanese, Korean, Turkish, Portuguese, and Icelandic.

## Bringing Data to Anvil

If your audio files live on a Mac with Apple Voice Memos, and Anvil is a separate machine:

### Option A: Transfer the database + audio files

```bash
# On Mac: export what Anvil needs
scp ~/tools/memoryatlas/data/atlas.db anvil:~/memoryatlas/data/
rsync -av ~/Library/Group\ Containers/group.com.apple.VoiceMemos.shared/Recordings/*.m4a anvil:~/memoryatlas/audio/
```

### Option B: Network mount (NAS)

Mount the NAS on both machines. Point `data_dir` at the shared path.

### Option C: Scan on Mac, process on Anvil

Run `atlas scan` and `atlas transcribe` on the Mac (needs Apple Voice Memos access + Apple Silicon for mlx-whisper). Then copy the database to Anvil for the heavy LLM work (`enrich` and `polish`).

```bash
# Mac: scan + transcribe
atlas scan && atlas transcribe

# Copy DB to Anvil
scp data/atlas.db anvil:~/memoryatlas/data/

# Anvil: enrich + polish (bigger models, more VRAM)
atlas enrich --model llama3.3:70b
atlas polish --language ru --model llama3.3:70b

# Copy results back
scp anvil:~/memoryatlas/data/atlas.db data/
atlas publish  # Regenerate Obsidian notes
```

## Stats (as of 2026-02-28)

| Metric | Value |
|--------|-------|
| Total voice memos | 929 |
| Total hours recorded | 675 |
| Transcribed | 799 (86%) |
| Enriched | 791 (99% of transcribed) |
| Polished (Russian) | 69/69 (100%) |
| Languages detected | 7 (EN, RU, JA, KO, TR, PT, IS) |

## License

Personal project by Peretz Partensky. Open source release pending.
