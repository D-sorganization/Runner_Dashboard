import { useCallback, useEffect, useLayoutEffect, type RefObject } from "react";

/**
 * #2654: autosizes a textarea to fit wrapped content (typing, prefill, draft
 * restore — all three flow through `text`, and container width changes) up
 * to `maxHeightPx`, then scrolls instead of clipping.
 */
export function useComposerAutosize(
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  text: string,
  containerSelector: string,
  maxHeightPx: number,
): void {
  // height = min(scrollHeight, cap); resetting to "auto" first forces an accurate
  // scrollHeight re-measure even when the previous render shrank the element.
  const recalcHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const natural = el.scrollHeight;
    el.style.height = `${Math.min(natural, maxHeightPx)}px`;
    el.style.overflowY = natural > maxHeightPx ? "auto" : "hidden";
  }, [textareaRef, maxHeightPx]);

  // useLayoutEffect (not useEffect) so the cap applies before paint.
  useLayoutEffect(() => {
    recalcHeight();
  }, [text, recalcHeight]);

  // Covers container width changes. Guarded against feedback loops: a resize
  // caused by our own height write doesn't change the container's width, so
  // the observer only re-fires on a genuine width change (skipped otherwise).
  useEffect(() => {
    const container = textareaRef.current?.closest(containerSelector);
    if (!container || typeof ResizeObserver === "undefined") return;
    let lastWidth: number | null = null;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width === undefined || width === lastWidth) return;
      lastWidth = width;
      recalcHeight();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [recalcHeight, textareaRef, containerSelector]);
}
