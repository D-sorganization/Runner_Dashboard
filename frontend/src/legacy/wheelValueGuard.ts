const WHEEL_VALUE_GUARD_OPT_OUT = "data-allow-wheel-value-change";

function isOptedOut(element: HTMLElement): boolean {
  return element.getAttribute(WHEEL_VALUE_GUARD_OPT_OUT) === "true";
}

function isWheelMutableInput(element: Element): element is HTMLInputElement {
  if (!(element instanceof HTMLInputElement)) {
    return false;
  }

  return (
    (element.type === "number" || element.type === "range") &&
    !element.disabled &&
    !element.readOnly &&
    !isOptedOut(element)
  );
}

function isWheelMutableSelect(element: Element): element is HTMLSelectElement {
  return element instanceof HTMLSelectElement && !element.disabled && !isOptedOut(element);
}

function isWheelMutableElement(element: Element | null): element is HTMLElement {
  if (!element) {
    return false;
  }

  return isWheelMutableInput(element) || isWheelMutableSelect(element);
}

function eventTargetsFocusedControl(eventTarget: EventTarget | null, activeElement: HTMLElement): boolean {
  return eventTarget instanceof Node && activeElement.contains(eventTarget);
}

export function installWheelValueGuard(root: Document = document): () => void {
  function onWheel(event: WheelEvent) {
    const activeElement = root.activeElement;
    if (!(activeElement instanceof HTMLElement)) {
      return;
    }

    if (!isWheelMutableElement(activeElement)) {
      return;
    }

    if (!eventTargetsFocusedControl(event.target, activeElement)) {
      return;
    }

    activeElement.blur();
  }

  root.addEventListener("wheel", onWheel, { capture: true, passive: true });

  return function cleanupWheelValueGuard() {
    root.removeEventListener("wheel", onWheel, true);
  };
}
