import { expect, type Locator, type TestInfo } from "@playwright/test";

export const SIDEBAR_FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export async function attachSidebarFocusState(
  testInfo: TestInfo,
  drawer: Locator,
  name: string,
): Promise<void> {
  let state: unknown;
  try {
    state = await drawer.evaluate((panel, selector) => {
      const describe = (element: Element | null) => {
        if (!element) return null;
        const ancestors: Array<Record<string, unknown>> = [];
        for (
          let node: Element | null = element;
          node;
          node = node.parentElement
        ) {
          const style = getComputedStyle(node);
          ancestors.push({
            tag: node.tagName.toLowerCase(),
            hidden: (node as HTMLElement).hidden,
            ariaHidden: node.getAttribute("aria-hidden"),
            inert: (node as HTMLElement).inert || node.hasAttribute("inert"),
            display: style.display,
            visibility: style.visibility,
            rectCount: node.getClientRects().length,
          });
        }
        const style = getComputedStyle(element);
        const label = element.getAttribute("aria-label") ?? "";
        const safeLabel = label.startsWith("Voice input")
          ? "Voice input"
          : ["Ask Barb", "Close Ask Barb", "Send message"].includes(label)
            ? label
            : label
              ? "other"
              : null;
        const candidate = element.getAttribute("data-focus-diagnostic");
        return {
          tag: element.tagName.toLowerCase(),
          safeLabel,
          diagnosticKind: [
            "css-visibility",
            "ancestor-aria-hidden",
            "inert",
          ].includes(candidate ?? "")
            ? candidate
            : null,
          active: element === document.activeElement,
          disabled:
            "disabled" in element
              ? Boolean((element as HTMLButtonElement).disabled)
              : false,
          hidden: (element as HTMLElement).hidden,
          ariaHidden: element.getAttribute("aria-hidden"),
          inert:
            (element as HTMLElement).inert || element.hasAttribute("inert"),
          display: style.display,
          visibility: style.visibility,
          rectCount: element.getClientRects().length,
          ancestors,
        };
      };
      const candidates = Array.from(
        panel.querySelectorAll<HTMLElement>(selector),
      );
      return {
        activeElement: describe(document.activeElement),
        candidates: candidates.map((element, index) => ({
          index,
          ...(describe(element) ?? {}),
        })),
      };
    }, SIDEBAR_FOCUSABLE);
  } catch {
    state = { diagnosticAvailable: false };
  }
  try {
    await testInfo.attach(name, {
      body: JSON.stringify(state, null, 2),
      contentType: "application/json",
    });
  } catch {
    // Preserve the original focus assertion if the drawer detached or attachment failed.
  }
}

export async function expectFocusWithState(
  testInfo: TestInfo,
  target: Locator,
  drawer: Locator,
  name: string,
): Promise<void> {
  try {
    await expect(target).toBeFocused();
  } catch (error) {
    await attachSidebarFocusState(testInfo, drawer, name);
    throw error;
  }
}
