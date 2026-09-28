/**
 * The Staff page must fit its column (#1718): index.css gives `.staff` a 16px
 * margin while StaffPage.css sizes it to 100%, which scrolled the page 32px
 * sideways and down. StaffPage.css owns the box: padding inside border-box.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.join(__dirname, "../StaffPage.css"), "utf8");
const staffBlock = css.match(/(^|\n)\.staff\s*\{([^}]*)\}/)?.[2] ?? "";

describe("Staff page box", () => {
  it("sizes to its column without an outer margin", () => {
    expect(staffBlock).toMatch(/width:\s*100%/);
    expect(staffBlock).toMatch(/box-sizing:\s*border-box/);
    expect(staffBlock).toMatch(/margin:\s*0/);
  });
});
