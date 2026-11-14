import { describe, expect, it } from "vitest";
import { safeHttpHref } from "../safeHttpHref";

describe("safeHttpHref", () => {
  it("allows http and https", () => {
    expect(safeHttpHref("https://github.com/o/r/issues/1")).toBe(
      "https://github.com/o/r/issues/1",
    );
    expect(safeHttpHref("http://127.0.0.1:8321/")).toBe("http://127.0.0.1:8321/");
  });

  it("rejects dangerous schemes", () => {
    expect(safeHttpHref("javascript:alert(1)")).toBeNull();
    expect(safeHttpHref("data:text/html,unsafe")).toBeNull();
    expect(safeHttpHref("vbscript:msgbox")).toBeNull();
  });
});
