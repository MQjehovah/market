const HTML_TAG_RE = /^\s*<(div|table|html|h1|h2|h3|p|section)\b/i
const INLINE_TAG_RE = /<(div|table|h2|h3|p)\b/i

export function pickHtmlOutput(out) {
  if (!out || typeof out !== 'object') return ''
  for (const [k, v] of Object.entries(out)) {
    if (typeof v === 'string' && /html/i.test(k) && INLINE_TAG_RE.test(v)) {
      const t = v.trim()
      return HTML_TAG_RE.test(t) ? t : `<div>${t}</div>`
    }
  }
  for (const v of Object.values(out)) {
    if (typeof v === 'string' && v.length > 200 && HTML_TAG_RE.test(v)) return v.trim()
  }
  return ''
}
