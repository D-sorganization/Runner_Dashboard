// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsoleErrorBanner } from "../ConsoleErrorBanner";

afterEach(cleanup);

describe("ConsoleErrorBanner", () => {
  it("leaves non-send failures visible and dismissible", () => {
    const onDismiss = vi.fn();
    render(<ConsoleErrorBanner error={{ kind: "thread", message: "Could not open" }} onDismiss={onDismiss} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not open the conversation");
    screen.getByRole("button", { name: "Dismiss error" }).click();
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
