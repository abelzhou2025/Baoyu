---
name: video-transcript-downloader
description: Download videos, audio, subtitles, and clean paragraph-style transcripts from YouTube and any other yt-dlp supported site. Use when asked to "download this video", "save this clip", "rip audio", "get subtitles", "get transcript", or to troubleshoot yt-dlp/ffmpeg and formats/playlists.
---

# Video Transcript Downloader

`./scripts/vtd.js` can:
- Print a transcript as a clean paragraph (timestamps optional).
- Download video/audio/subtitles.

Transcript behavior:
- **Default output**: Article-style paragraphs with video title, 4-5 sentences per paragraph, breaks only after complete sentences.
- YouTube: fetch via `youtube-transcript-plus` when possible.
- Otherwise: pull subtitles via `yt-dlp`, then clean into paragraphs.

## Setup

```bash
cd /Users/abelzhou/.claude/skills/video-transcript-downloader && npm ci
```

## Transcript (default: readable paragraphs)

```bash
./scripts/vtd.js transcript --url 'https://…'
./scripts/vtd.js transcript --url 'https://…' --lang en
./scripts/vtd.js transcript --url 'https://…' --timestamps
./scripts/vtd.js transcript --url 'https://…' --segments
./scripts/vtd.js transcript --url 'https://…' --keep-brackets
```

**Output formats:**
- **Default**: Article-style paragraphs (recommended), with video title as markdown heading
- `--timestamps`: Show timestamps with each line
- `--segments`: One segment per line (fragmented)
- `--keep-brackets`: Preserve bracketed cues like `[Music]`

## Download with title as filename

```bash
./scripts/download-transcript-with-title.js 'https://…' [output-dir]
```

This will:
- Fetch the video title
- Use it as the filename
- Add it as a markdown heading in the file
- Save to `~/Desktop/Peter Steinberger/` by default

Example:
```bash
node scripts/download-transcript-with-title.js 'https://www.youtube.com/watch?v=xxx'
```

## Download video / audio / subtitles

```bash
./scripts/vtd.js download --url 'https://…' --output-dir ~/Downloads
./scripts/vtd.js audio --url 'https://…' --output-dir ~/Downloads
./scripts/vtd.js subs --url 'https://…' --output-dir ~/Downloads --lang en
```

## Formats (list + choose)

List available formats (format ids, resolution, container, audio-only, etc):

```bash
./scripts/vtd.js formats --url 'https://…'
```

Download a specific format id (example):

```bash
./scripts/vtd.js download --url 'https://…' --output-dir ~/Downloads -- --format 137+140
```

Prefer MP4 container without re-encoding (remux when possible):

```bash
./scripts/vtd.js download --url 'https://…' --output-dir ~/Downloads -- --remux-video mp4
```

## Notes

- Default transcript output is article-style paragraphs with video title. This is the recommended format for readability.
- Paragraphs break only after complete sentences (4-5 sentences per paragraph).
- Bracketed cues like `[Music]` are stripped by default; keep them via `--keep-brackets`.
- Pass extra `yt-dlp` args after `--` for `transcript` fallback, `download`, `audio`, `subs`, `formats`.

```bash
./scripts/vtd.js formats --url 'https://…' -- -v
```

## Troubleshooting (only when needed)

- Missing `yt-dlp` / `ffmpeg`:

```bash
brew install yt-dlp ffmpeg
```

- Verify:

```bash
yt-dlp --version
ffmpeg -version | head -n 1
```
