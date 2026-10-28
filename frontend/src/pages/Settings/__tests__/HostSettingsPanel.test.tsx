// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import * as hostSettingsApi from "../hostSettingsApi";
import { HostSettingsPanel } from "../HostSettingsPanel";

const SAMPLE: hostSettingsApi.HostSettingsResponse = {
  path: "/tmp/host_settings.json",
  settings: {
    schema_version: 1,
    global: {
      preference: "grokbot-first",
      hosts: { grokbot: { enabled: true }, rd: { enabled: true } },
    },
    roles: {},
  },
  resolved: [
    {
      role: "barb",
      preference: "grokbot-first",
      leader: "grokbot",
      followers: [],
      hosts_enabled: { grokbot: true, rd: true },
    },
    {
      role: "night-watch",
      preference: "grokbot-first",
      leader: "grokbot",
      followers: [],
      hosts_enabled: { grokbot: true, rd: true },
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HostSettingsPanel (#3101)", () => {
  it("shows resulting leader per role and requires confirmation to save", async () => {
    vi.spyOn(hostSettingsApi, "fetchHostSettings").mockResolvedValue(SAMPLE);
    const put = vi.spyOn(hostSettingsApi, "putHostSettings").mockResolvedValue({
      ...SAMPLE,
      settings: {
        ...SAMPLE.settings,
        roles: { barb: { preference: "rd-first", hosts: SAMPLE.settings.global.hosts } },
      },
      resolved: SAMPLE.resolved.map((r) =>
        r.role === "barb" ? { ...r, preference: "rd-first", leader: "rd" } : r,
      ),
    });

    render(<HostSettingsPanel />);
    await waitFor(() => expect(screen.getByTestId("host-settings-panel")).toBeInTheDocument());

    expect(screen.getByTestId("host-settings-leader-barb")).toHaveTextContent("grokbot");

    const row = screen.getByTestId("host-settings-row-barb");
    fireEvent.change(within(row).getByLabelText(/preference for barb/i), {
      target: { value: "rd-first" },
    });

    expect(screen.getByTestId("host-settings-leader-barb")).toHaveTextContent("rd");

    fireEvent.click(screen.getByTestId("host-settings-save"));
    expect(screen.getByTestId("host-settings-confirm")).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("host-settings-confirm"));
    await waitFor(() => expect(put).toHaveBeenCalledWith(expect.objectContaining({ schema_version: 1 }), true));
  });
});
