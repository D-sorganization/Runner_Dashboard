/**
 * QuickDispatch.tsx — the global "⚡ Quick Dispatch" toolbar popover, extracted
 * (behaviour-wise 1:1) from the legacy `App.tsx` monolith as part of the
 * decomposition epic (#836, pass 9).
 *
 * Self-contained: takes no props. Opens a fixed-position dialog that lazily
 * fetches the repo list (`GET /api/repos`) and provider list
 * (`GET /api/agents/providers`), validates a prompt (>= 10 chars) and a
 * repository, and POSTs an ad-hoc dispatch to `/api/agents/quick-dispatch`.
 * Closes on outside-click and Escape. The render is preserved verbatim using
 * the legacy `h = React.createElement` factory (allowed during migration). The
 * provider/model registry now lives in `lib/providerModels`.
 */
import React from "react";
import { legacyFetch } from "../lib/api";
import { PROVIDER_MODELS, PROVIDERS_WITH_MODEL } from "../lib/providerModels";

/* eslint-disable @typescript-eslint/no-explicit-any */
const h = React.createElement as any;

export function QuickDispatchPopover() {
  const os = React.useState(false);
  const open = os[0], setOpen = os[1];

  const rs = React.useState<any[]>([]);
  const repoList = rs[0], setRepoList = rs[1];

  const ps = React.useState<string[]>([]);
  const providerList = ps[0], setProviderList = ps[1];

  const fms = React.useState({
    repository: "",
    provider: "claude_code_cli",
    model: "claude-sonnet-4-6",
    ref: "main",
    prompt: "",
  });
  const form = fms[0], setForm = fms[1];

  const ls = React.useState(false);
  const loading = ls[0], setLoading = ls[1];

  const es = React.useState<string | null>(null);
  const error = es[0], setError = es[1];

  const ss = React.useState<string | null>(null);
  const successMsg = ss[0], setSuccessMsg = ss[1];

  const popoverRef = React.useRef<any>(null);
  const triggerRef = React.useRef<any>(null);

  // Close on outside click and Escape
  React.useEffect(function () {
    if (!open) return;
    function onMouseDown(e: any) {
      if (
        popoverRef.current && !popoverRef.current.contains(e.target) &&
        triggerRef.current && !triggerRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    }
    function onKeyDown(e: any) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return function () {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
    // Behaviour-preserving: listeners are (re)bound only on open toggle, as in
    // the original legacy effect. setOpen is a stable setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Fetch repos and providers when popover opens
  React.useEffect(function () {
    if (!open) return;
    if (repoList.length === 0) {
      legacyFetch("/api/repos")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          const repos = (d && d.repos) ? d.repos : [];
          setRepoList(repos);
          if (repos.length > 0 && !form.repository) {
            setForm(function (prev) {
              return Object.assign({}, prev, { repository: repos[0].full_name || repos[0].name || "" });
            });
          }
        })
        .catch(function () {});
    }
    if (providerList.length === 0) {
      legacyFetch("/api/agents/providers")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          const providers = d && d.providers ? Object.keys(d.providers) : ["claude_code_cli"];
          setProviderList(providers);
        })
        .catch(function () {
          setProviderList(["claude_code_cli", "jules_api", "codex_cli", "gemini_cli"]);
        });
    }
    // Behaviour-preserving: the lazy fetch fires once per open, guarded by the
    // list-length checks above, exactly as in the original legacy effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function handleToggle() {
    setOpen(function (prev) { return !prev; });
    setError(null);
    setSuccessMsg(null);
  }

  function handleFormChange(field: string, value: string) {
    if (field === "provider") {
      const modelList = PROVIDER_MODELS[value] || [];
      setForm(function (prev) {
        return Object.assign({}, prev, {
          provider: value,
          model: modelList.length ? modelList[0].value : prev.model,
        });
      });
      return;
    }
    setForm(function (prev) { return Object.assign({}, prev, { [field]: value }); });
  }

  function handleCancel() {
    setOpen(false);
    setError(null);
    setSuccessMsg(null);
  }

  function handleDispatch() {
    setError(null);
    if (!form.repository) {
      setError("Please select a repository.");
      return;
    }
    if (!form.prompt || form.prompt.trim().length < 10) {
      setError("Prompt must be at least 10 characters.");
      return;
    }
    setLoading(true);
    const body: any = {
      repository: form.repository,
      prompt: form.prompt.trim(),
      provider: form.provider,
      ref: form.ref || "main",
      task_kind: "adhoc",
    };
    if (PROVIDERS_WITH_MODEL.indexOf(form.provider) !== -1 && form.model.trim()) {
      body.model = form.model.trim();
    }
    legacyFetch("/api/agents/quick-dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json().then(function (d: any) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .then(function (result) {
        setLoading(false);
        if (!result.ok) {
          if (result.status === 429) {
            setError("Rate limited. Try again in a moment.");
          } else {
            setError((result.data && result.data.detail) || "Dispatch failed.");
          }
          return;
        }
        setSuccessMsg("✓ Dispatched!");
        setForm(function (prev) {
          return Object.assign({}, prev, { prompt: "" });
        });
        setTimeout(function () {
          setOpen(false);
          setSuccessMsg(null);
        }, 1800);
      })
      .catch(function () {
        setLoading(false);
        setError("Network error. Please try again.");
      });
  }

  const showModel = PROVIDERS_WITH_MODEL.indexOf(form.provider) !== -1;

  const labelStyle: any = {
    fontSize: 12,
    color: "var(--text-muted)",
    marginBottom: 3,
    display: "block",
  };
  const inputStyle: any = {
    width: "100%",
    background: "var(--bg-primary)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: "5px 10px",
    color: "var(--text-primary)",
    fontSize: 13,
    outline: "none",
    boxSizing: "border-box",
  };
  const rowStyle: any = { marginBottom: 10 };

  return h(
    "div",
    { style: { position: "relative", display: "inline-block" } },
    h(
      "button",
      {
        ref: triggerRef,
        className: "btn btn-blue",
        style: {
          fontSize: 13,
          padding: "6px 12px",
          fontWeight: 600,
          background: "rgba(88,166,255,0.15)",
        },
        onClick: handleToggle,
        title: "Open Quick Dispatch",
        "aria-label": "Open Quick Dispatch",
        "aria-expanded": open,
      },
      "⚡ Quick Dispatch ▾",
    ),
    open
      ? h(
          "div",
          {
            ref: popoverRef,
            role: "dialog",
            "aria-modal": "true",
            "aria-label": "Quick Dispatch",
            style: {
              position: "fixed",
              right: 16,
              top: 64,
              width: "calc(100vw - 32px)",
              maxWidth: 320,
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
              zIndex: 9000,
              padding: 16,
            },
          },
          h(
            "div",
            { style: { fontWeight: 700, fontSize: 14, marginBottom: 14, color: "var(--text-primary)" } },
            "⚡ Quick Dispatch",
          ),
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Repository"),
            h(
              "select",
              {
                style: inputStyle,
                value: form.repository,
                onChange: function (e: any) { handleFormChange("repository", e.target.value); },
              },
              repoList.length === 0
                ? h("option", { value: "" }, "Loading…")
                : repoList.map(function (repo: any) {
                    const name = repo.full_name || repo.name || repo;
                    return h("option", { key: name, value: name }, name);
                  }),
            ),
          ),
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Provider"),
            h(
              "select",
              {
                style: inputStyle,
                value: form.provider,
                onChange: function (e: any) { handleFormChange("provider", e.target.value); },
              },
              providerList.length === 0
                ? h("option", { value: "claude_code_cli" }, "Claude Code CLI")
                : providerList.map(function (pid: string) {
                    const labels: any = {
                      claude_code_cli: "Claude Code CLI",
                      codex_cli: "Codex CLI",
                      gemini_cli: "Gemini CLI",
                      jules_api: "Jules API",
                      ollama: "Ollama",
                      cline: "Cline",
                    };
                    return h("option", { key: pid, value: pid }, labels[pid] || pid);
                  }),
            ),
          ),
          showModel
            ? h(
                "div",
                { style: rowStyle },
                h("label", { style: labelStyle }, "Model"),
                (function() {
                  const modelOpts = PROVIDER_MODELS[form.provider];
                  if (modelOpts && modelOpts.length > 0) {
                    return h("select", {
                      style: inputStyle,
                      value: form.model,
                      onChange: function (e: any) { handleFormChange("model", e.target.value); },
                    },
                      modelOpts.map(function(m) {
                        return h("option", { key: m.value, value: m.value }, m.label);
                      })
                    );
                  }
                  return h("input", {
                    type: "text",
                    style: inputStyle,
                    value: form.model,
                    placeholder: "model name",
                    onChange: function (e: any) { handleFormChange("model", e.target.value); },
                  });
                })(),
              )
            : null,
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Branch ref"),
            h("input", {
              type: "text",
              style: inputStyle,
              value: form.ref,
              placeholder: "main",
              onChange: function (e: any) { handleFormChange("ref", e.target.value); },
            }),
          ),
          h(
            "div",
            { style: rowStyle },
            h("label", { style: labelStyle }, "Prompt"),
            h("textarea", {
              rows: 4,
              style: Object.assign({}, inputStyle, { resize: "vertical", fontFamily: "inherit" }),
              value: form.prompt,
              placeholder: "Describe the task for the agent…",
              onChange: function (e: any) { handleFormChange("prompt", e.target.value); },
            }),
          ),
          error
            ? h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--accent-red)",
                    marginBottom: 10,
                    padding: "6px 10px",
                    background: "rgba(248,81,73,0.1)",
                    borderRadius: 4,
                    border: "1px solid rgba(248,81,73,0.3)",
                  },
                },
                error,
              )
            : null,
          successMsg
            ? h(
                "div",
                {
                  style: {
                    fontSize: 12,
                    color: "var(--accent-green)",
                    marginBottom: 10,
                    padding: "6px 10px",
                    background: "rgba(63,185,80,0.1)",
                    borderRadius: 4,
                    border: "1px solid rgba(63,185,80,0.3)",
                  },
                },
                successMsg,
              )
            : null,
          h(
            "div",
            { style: { display: "flex", justifyContent: "flex-end", gap: 8 } },
            h(
              "button",
              { className: "btn", onClick: handleCancel, disabled: loading },
              "Cancel",
            ),
            h(
              "button",
              {
                className: "btn btn-blue",
                style: { background: "rgba(88,166,255,0.2)", fontWeight: 600 },
                onClick: handleDispatch,
                disabled: loading,
              },
              loading ? "Dispatching…" : "⚡ Dispatch",
            ),
          ),
        )
      : null,
  );
}
