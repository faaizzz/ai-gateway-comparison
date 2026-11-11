// OpenAI Chat Completions request body. Deterministic, so every target gets the
// same bytes. `promptChars` pads the user message (payload sweeps, S5).
const FILLER = 'The quick brown fox jumps over the lazy dog. ';

export function chatBody({ model, stream = false, promptChars = 0, maxTokens = 0 }) {
  let content = 'Say hello in one short sentence.';
  if (promptChars > 0) {
    content += ' ' + FILLER.repeat(Math.ceil(promptChars / FILLER.length)).slice(0, promptChars);
  }
  const body = { model, stream, messages: [{ role: 'user', content }] };
  if (stream) body.stream_options = { include_usage: true };
  if (maxTokens > 0) body.max_tokens = maxTokens;
  return JSON.stringify(body);
}

// A non-streaming body of about `bytes` bytes in total (the JSON as sent), for the payload
// sweep (S5). The padding is deterministic text in the user message, so every target gets the
// same bytes.
export function chatBodyOfSize({ model, bytes }) {
  const base = chatBody({ model }).length;
  // chatBody adds one space plus promptChars characters
  return chatBody({ model, promptChars: Math.max(1, bytes - base - 1) });
}
