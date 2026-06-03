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
import { TouchButton } from "../primitives/TouchButton";

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

  return h(
    "div",
    { className: "quick-dispatch" },
    h(
      TouchButton,
      {
        ref: triggerRef,
        className: "quick-dispatch__trigger",
        onClick: handleToggle,
        title: "Open Quick Dispatch",
        "aria-label": "Open Quick Dispatch",
        "aria-expanded": open,
        variant: "primary",
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
            className: "quick-dispatch__popover",
          },
          h(
            "div",
            { className: "quick-dispatch__heading" },
            "⚡ Quick Dispatch",
          ),
          h(
            "div",
            { className: "quick-dispatch__field" },
            h("label", { className: "quick-dispatch__label" }, "Repository"),
            h(
              "select",
              {
                className: "quick-dispatch__input",
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
            { className: "quick-dispatch__field" },
            h("label", { className: "quick-dispatch__label" }, "Provider"),
            h(
              "select",
              {
                className: "quick-dispatch__input",
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
                { className: "quick-dispatch__field" },
                h("label", { className: "quick-dispatch__label" }, "Model"),
                (function() {
                  const modelOpts = PROVIDER_MODELS[form.provider];
                  if (modelOpts && modelOpts.length > 0) {
                    return h("select", {
                      className: "quick-dispatch__input",
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
                    className: "quick-dispatch__input",
                    value: form.model,
                    placeholder: "model name",
                    onChange: function (e: any) { handleFormChange("model", e.target.value); },
                  });
                })(),
              )
            : null,
          h(
            "div",
            { className: "quick-dispatch__field" },
            h("label", { className: "quick-dispatch__label" }, "Branch ref"),
            h("input", {
              type: "text",
              className: "quick-dispatch__input",
              value: form.ref,
              placeholder: "main",
              onChange: function (e: any) { handleFormChange("ref", e.target.value); },
            }),
          ),
          h(
            "div",
            { className: "quick-dispatch__field" },
            h("label", { className: "quick-dispatch__label" }, "Prompt"),
            h("textarea", {
              rows: 4,
              className: "quick-dispatch__input quick-dispatch__textarea",
              value: form.prompt,
              placeholder: "Describe the task for the agent…",
              onChange: function (e: any) { handleFormChange("prompt", e.target.value); },
            }),
          ),
          error
            ? h(
                "div",
                { className: "quick-dispatch__status quick-dispatch__status--error" },
                error,
              )
            : null,
          successMsg
            ? h(
                "div",
                { className: "quick-dispatch__status quick-dispatch__status--success" },
                successMsg,
              )
            : null,
          h(
            "div",
            { className: "quick-dispatch__actions" },
            h(
              TouchButton,
              { onClick: handleCancel, disabled: loading },
              "Cancel",
            ),
            h(
              TouchButton,
              {
                onClick: handleDispatch,
                disabled: loading,
                variant: "primary",
              },
              loading ? "Dispatching…" : "⚡ Dispatch",
            ),
          ),
        )
      : null,
  );
}
