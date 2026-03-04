const YouTubeTranscript = require('youtube-transcript-plus');
const fs = require('fs');

const url = 'https://www.youtube.com/watch?v=qe9QSCF-d88';

async function downloadTranscript() {
  try {
    console.log('Downloading transcript...');

    // Try to get transcript with different configs
    let transcript;
    const configs = [
      { lang: 'en' },
      { lang: 'en-US' },
      {} // fallback to auto
    ];

    for (const config of configs) {
      try {
        transcript = await YouTubeTranscript.fetchTranscript(url, config);
        if (transcript && transcript.length > 0) {
          console.log(`✓ Got transcript with config:`, config);
          break;
        }
      } catch (e) {
        console.log(`Failed with config`, config, `trying next...`);
      }
    }

    if (!transcript || transcript.length === 0) {
      throw new Error('No transcript found');
    }

    processTranscript(transcript);

  } catch (error) {
    console.error('Error:', error.message);
    process.exit(1);
  }
}

function processTranscript(transcript) {
  // Extract text and join
  const text = transcript.map(item => item.text).join(' ');

  // Check if it's English (has common English words)
  const englishWords = ['the', 'and', 'is', 'to', 'of', 'in', 'that', 'it', 'with', 'as'];
  const words = text.toLowerCase().split(/\s+/);
  const englishCount = words.filter(w => englishWords.includes(w)).length;

  console.log(`English word ratio: ${englishCount}/${words.length} (${(englishCount/words.length*100).toFixed(1)}%)`);

  // Clean up extra spaces
  const cleanText = text.replace(/\s+/g, ' ').trim();

  // Split into sentences
  const sentences = cleanText.match(/[^.!?]+[.!?]+/g) || [cleanText];

  // Group into paragraphs (5 sentences each)
  const paragraphs = [];
  for (let i = 0; i < sentences.length; i += 5) {
    const para = sentences.slice(i, i + 5).join(' ');
    paragraphs.push(para);
  }

  // Write markdown
  const markdown = `# The Catastrophic Risks of AI — and a Safer Path | Yoshua Bengio | TED

${paragraphs.join('\n\n')}
`;

  const outputPath = `/Users/abelzhou/Desktop/The_Catastrophic_Risks_of_AI_Yoshua_Bengio_TED_English.md`;
  fs.writeFileSync(outputPath, markdown, 'utf-8');

  console.log(`✓ Done!`);
  console.log(`Output: ${outputPath}`);
  console.log(`Paragraphs: ${paragraphs.length}`);
  console.log(`Words: ${cleanText.split(/\s+/).length}`);
}

downloadTranscript();
