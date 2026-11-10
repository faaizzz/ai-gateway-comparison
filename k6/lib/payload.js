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
