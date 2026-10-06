// Scans backward from the caret for the nearest un-terminated `@`/`#`
// token (flomo-style inline mention trigger) -- used by QuickEntryBox to
// decide whether to show MentionAutocomplete while typing, and to know
// which substring of the text to replace once a suggestion is picked.
// A trigger is "active" only if there's no whitespace between the `@`/`#`
// and the caret (an already-finished word doesn't reopen the dropdown).
export function findActiveMentionTrigger(text, caretIndex) {
  const upTo = text.slice(0, caretIndex)
  const match = /(?:^|\s)([@#])([^\s@#]*)$/.exec(upTo)
  if (!match) return null
  const [whole, marker, query] = match
  const start = upTo.length - whole.length + (whole.length - (marker.length + query.length))
  return { type: marker, query, start }
}
