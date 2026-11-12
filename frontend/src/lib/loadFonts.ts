/**
 * Non-blocking web-font loading (#2990).
 *
 * A static <link rel="stylesheet"> to Google Fonts is render-blocking. The
 * hub's CSP forbids inline handlers, so the stylesheet is attached from the
 * bundle after first paint instead. `display=swap` keeps text visible on the
 * system fallback stack until the fonts arrive. Only weights in use are listed.
 */
export const FONTS_HREF =
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap'

export function loadFonts(doc: Document = document): void {
  if (doc.querySelector('link[data-fonts="google"]')) return
  const link = doc.createElement('link')
  link.rel = 'stylesheet'
  link.href = FONTS_HREF
  link.setAttribute('data-fonts', 'google')
  doc.head.appendChild(link)
}
