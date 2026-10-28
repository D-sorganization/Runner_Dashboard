/**
 * #2654: height = min(scrollHeight, cap) on content change and container
 * width change. jsdom never computes layout, so scrollHeight is mocked
 * per-test on HTMLTextAreaElement.prototype (restored in afterAll).
 */
import "@testing-library/jest-dom/vitest";
import React, { useRef } from "react";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { useComposerAutosize } from "../useComposerAutosize";

const MAX_HEIGHT_PX = 220;

function Harness({ text }: { text: string }) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useComposerAutosize(textareaRef, text, ".harness-box", MAX_HEIGHT_PX);
  return (
    <div className="harness-box">
      <textarea ref={textareaRef} readOnly value={text} aria-label="harness-textarea" />
    </div>
  );
}

describe("useComposerAutosize", () => {
  let mockScrollHeight = 0;
  let originalScrollHeightDescriptor: PropertyDescriptor | undefined;

  beforeAll(() => {
    originalScrollHeightDescriptor = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "scrollHeight",
    );
    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
      configurable: true,
      get() {
        return mockScrollHeight;
      },
    });
  });

  afterAll(() => {
    if (originalScrollHeightDescriptor) {
      Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", originalScrollHeightDescriptor);
    } else {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (HTMLTextAreaElement.prototype as any).scrollHeight;
    }
  });

  beforeEach(() => {
    mockScrollHeight = 0;
  });

  it("grows to fit content below the cap", () => {
    mockScrollHeight = 120;
    render(<Harness text="Wrapped text that spans multiple lines" />);
    const textarea = screen.getByLabelText("harness-textarea") as HTMLTextAreaElement;

    expect(textarea.style.height).toBe("120px");
    expect(textarea.style.overflowY).toBe("hidden");
  });

  it("scrolls above the cap", () => {
    mockScrollHeight = 400;
    render(<Harness text={"x".repeat(500)} />);
    const textarea = screen.getByLabelText("harness-textarea") as HTMLTextAreaElement;

    expect(textarea.style.height).toBe("220px");
    expect(textarea.style.overflowY).toBe("auto");
  });

  it("recalculates on width change without a resize loop", () => {
    type ResizeCallback = (entries: Array<{ contentRect: { width: number } }>) => void;
    let capturedCb: ResizeCallback | null = null;
    class FakeResizeObserver {
      constructor(cb: ResizeCallback) {
        capturedCb = cb;
      }
      observe(): void {}
      disconnect(): void {}
    }
    const originalResizeObserver = globalThis.ResizeObserver;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).ResizeObserver = FakeResizeObserver;

    try {
      mockScrollHeight = 50;
      render(<Harness text="initial" />);
      const textarea = screen.getByLabelText("harness-textarea") as HTMLTextAreaElement;
      expect(textarea.style.height).toBe("50px");
      expect(capturedCb).not.toBeNull();

      // Genuine width change recomputes height from the new scrollHeight.
      mockScrollHeight = 90;
      capturedCb!([{ contentRect: { width: 300 } }]);
      expect(textarea.style.height).toBe("90px");

      // Same width again must be a no-op (the dedup guard that prevents a
      // resize -> recalc -> resize feedback loop), even though content
      // "changed" underneath it.
      mockScrollHeight = 150;
      capturedCb!([{ contentRect: { width: 300 } }]);
      expect(textarea.style.height).toBe("90px");
    } finally {
      globalThis.ResizeObserver = originalResizeObserver;
    }
  });
});
