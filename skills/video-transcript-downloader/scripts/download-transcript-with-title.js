#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { YoutubeTranscript } from "youtube-transcript-plus";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function die(message, code = 1) {
  process.stderr.write(String(message).trimEnd() + "\n");
  process.exit(code);
}

function which(cmd) {
  const envPath = process.env.PATH || "";
  const parts = envPath.split(path.delimiter);
  for (const p of parts) {
    const full = path.join(p, cmd);
    if (fs.existsSync(full)) return full;
  }
  return null;
}

function resolveBin(name, fallback) {
  return which(name) || (fallback && fs.existsSync(fallback) ? fallback : null);
}

function run(cmd, args, { cwd } = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (out += d.toString()));
    child.on("close", (code) => resolve({ code, out }));
  });
}

function extractYouTubeId(input) {
  if (!input) return null;
  const raw = String(input).trim();
  if (/^[a-zA-Z0-9_-]{11}$/.test(raw)) return raw;
  const m = raw.match(/(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
  return m ? m[1] : null;
}

function sanitizeFilename(title) {
  if (!title) return "transcript";
  return title
    .replace(/[<>:"/\\|?*]/g, '')
    .replace(/\s+/g, ' ')
    .replace(/^\.+$/, '')
    .slice(0, 200);
}

async function getVideoTitle(url) {
  // Try YouTube oEmbed API first (no API key needed)
  const videoId = extractYouTubeId(url);
  if (videoId) {
    try {
      const response = await fetch(`https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=${videoId}&format=json`);
      if (response.ok) {
        const data = await response.json();
        return data.title;
      }
    } catch (e) {
      // Fall through to yt-dlp
    }
  }

  // Fallback to yt-dlp
  const ytdlp = resolveBin("yt-dlp", "/opt/homebrew/bin/yt-dlp");
  if (!ytdlp) return null;

  const args = ["--get", "title", "--no-playlist", url];
  const r = await run(ytdlp, args);

  if (r.code !== 0) return null;

  const lines = r.out.split("\n").map(l => l.trim()).filter(l => l);
  return lines[0] || null;
}

async function downloadTranscriptWithTitle(url, outputDir) {
  // Get video title and ID
  const title = await getVideoTitle(url);
  const safeTitle = sanitizeFilename(title);
  const videoId = extractYouTubeId(url);

  // Create output directory
  fs.mkdirSync(outputDir, { recursive: true });

  // Define output file
  const outputFile = path.join(outputDir, `${safeTitle}.md`);

  console.error(`Downloading transcript for: ${title}`);
  console.error(`Output file: ${outputFile}`);

  // Get current date
  const date = new Date().toISOString().split('T')[0];

  // Run vtd.js transcript command
  const vtdScript = path.join(__dirname, "vtd.js");
  const args = ["transcript", "--url", url];
  const child = spawn("node", [vtdScript, ...args], { stdio: ["ignore", "pipe", "inherit"] });

  let content = "";
  child.stdout.on("data", (d) => (content += d.toString()));

  child.on("close", (code) => {
    if (code !== 0) {
      die("Failed to download transcript");
    }

    // Remove duplicate title if vtd.js already added one
    // vtd.js adds "# ${title}\n\n" at the beginning
    const lines = content.split('\n');
    let startIndex = 0;

    // Skip the first line if it's a duplicate title
    if (lines.length > 0 && lines[0].trim() === `# ${title}`) {
      startIndex = 1; // Skip duplicate title
    }

    const transcriptContent = lines.slice(startIndex).join('\n').trim();

    // Write to file with metadata header
    const fileContent = `---
title: ${title}
url: ${url}
video_id: ${videoId}
type: transcript
date: ${date}
source: YouTube
---

# ${title}

${transcriptContent}
`;

    fs.writeFileSync(outputFile, fileContent, "utf8");

    console.error(`✓ Done: ${outputFile}`);
    console.log(outputFile);
  });
}

// Main
const url = process.argv[2];
const outputDir = process.argv[3] || path.join(process.env.HOME, "Desktop", "Peter Steinberger");

if (!url) {
  console.error("Usage: node download-transcript-with-title.js <youtube-url> [output-dir]");
  process.exit(1);
}

downloadTranscriptWithTitle(url, outputDir);
