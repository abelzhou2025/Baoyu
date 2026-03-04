#!/usr/bin/env bash

# Video Transcript Downloader with Title-based Filename
# Usage: ./download-transcript-with-title.sh <youtube-url> [output-dir]

set -e

URL="$1"
OUTPUT_DIR="${2:-$HOME/Desktop/Peter Steinberger}"

if [ -z "$URL" ]; then
  echo "Usage: $0 <youtube-url> [output-dir]"
  exit 1
fi

# Get video title using yt-dlp
TITLE=$(yt-dlp --get-title --no-playlist "$URL" 2>/dev/null | head -n 1)

if [ -z "$TITLE" ]; then
  echo "Error: Could not fetch video title"
  exit 1
fi

# Sanitize title for filename
SAFE_TITLE=$(echo "$TITLE" | \
  sed 's/[<>:"|?*]//g' | \
  sed 's/[\/\\]/-/g' | \
  sed 's/\s+/ /g' | \
  sed 's/^\.+//' | \
  cut -c1-200)

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Define output file
OUTPUT_FILE="$OUTPUT_DIR/${SAFE_TITLE}.md"

echo "Downloading transcript for: $TITLE"
echo "Output file: $OUTPUT_FILE"

# Download transcript with title header
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
node "$SCRIPT_DIR/vtd.js" transcript --url "$URL" --readable > "$OUTPUT_FILE"

echo "✓ Done: $OUTPUT_FILE"
