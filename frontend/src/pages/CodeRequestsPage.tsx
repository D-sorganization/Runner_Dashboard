import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import {
  CodeRequestsTab,
  type CodeDispatchPayload,
  type CodeRepo,
  type CodeRequestRecord,
  type DispatchTargetStatus,
  type PromptNotes,
  type PromptTemplate,
} from "./CodeRequests";

interface ReposPayload {
  repos?: CodeRepo[];
}

interface RequestsPayload {
  requests?: CodeRequestRecord[];
  dispatchTarget?: DispatchTargetStatus;
}

interface TemplatesPayload {
  templates?: PromptTemplate[];
  promptNotes?: PromptNotes;
}

const EMPTY_PROMPT_NOTES: PromptNotes = { notes: "", enabled: true };

function normalizeReposPayload(payload: unknown): CodeRepo[] {
  if (Array.isArray(payload)) return payload as CodeRepo[];
  if (payload && typeof payload === "object") {
    const repos = (payload as ReposPayload).repos;
    if (Array.isArray(repos)) return repos;
  }
  return [];
}

function normalizeRequestsPayload(payload: unknown): CodeRequestRecord[] {
  if (Array.isArray(payload)) return payload as CodeRequestRecord[];
  if (payload && typeof payload === "object") {
    const requests = (payload as RequestsPayload).requests;
    if (Array.isArray(requests)) return requests;
  }
  return [];
}

function normalizeDispatchTarget(payload: unknown): DispatchTargetStatus | undefined {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return undefined;
  return (payload as RequestsPayload).dispatchTarget;
}

function normalizeTemplatesPayload(payload: unknown): {
  templates: PromptTemplate[];
  promptNotes: PromptNotes;
} {
  if (!payload || typeof payload !== "object") {
    return { templates: [], promptNotes: EMPTY_PROMPT_NOTES };
  }
  const raw = payload as TemplatesPayload;
  return {
    templates: Array.isArray(raw.templates) ? raw.templates : [],
    promptNotes: raw.promptNotes ?? EMPTY_PROMPT_NOTES,
  };
}

function parseJsonOrThrow(response: Response, fallback: string): Promise<unknown> {
  return response.json().then((data: unknown) => {
    if (!response.ok) {
      const detail =
        data && typeof data === "object" && "detail" in data
          ? String((data as { detail?: unknown }).detail)
          : fallback;
      throw new Error(detail);
    }
    return data;
  });
}

export function CodeRequestsPage(): React.ReactElement {
  const [repos, setRepos] = useState<CodeRepo[]>([]);
  const [requests, setRequests] = useState<CodeRequestRecord[]>([]);
  const [dispatchTarget, setDispatchTarget] = useState<DispatchTargetStatus | undefined>();
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [promptNotes, setPromptNotes] = useState<PromptNotes>(EMPTY_PROMPT_NOTES);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    Promise.all([
      legacyFetch("/api/repos", { signal })
        .then((r) => parseJsonOrThrow(r, "repos failed"))
        .catch(() => ({ repos: [] })),
      legacyFetch("/api/code-requests", { signal })
        .then((r) => parseJsonOrThrow(r, "code requests failed"))
        .catch(() => ({ requests: [] })),
      legacyFetch("/api/code-requests/templates", { signal })
        .then((r) => parseJsonOrThrow(r, "templates failed"))
        .catch(() => ({
          templates: [],
          promptNotes: EMPTY_PROMPT_NOTES,
        })),
    ])
      .then(([reposPayload, requestsPayload, templatesPayload]) => {
        const normalizedTemplates = normalizeTemplatesPayload(templatesPayload);
        setRepos(normalizeReposPayload(reposPayload));
        setRequests(normalizeRequestsPayload(requestsPayload));
        setDispatchTarget(normalizeDispatchTarget(requestsPayload));
        setTemplates(normalizedTemplates.templates);
        setPromptNotes(normalizedTemplates.promptNotes);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const dispatchCodeRequest = useCallback((payload: CodeDispatchPayload) => {
    return legacyFetch("/api/code-requests/dispatch", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    }).then((r) => parseJsonOrThrow(r, "dispatch failed"));
  }, []);

  const savePromptTemplate = useCallback(
    (template: PromptTemplate) => {
      return legacyFetch("/api/code-requests/templates", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(template),
      })
        .then((r) => parseJsonOrThrow(r, "save failed"))
        .then((data) => {
          refresh();
          return data;
        });
    },
    [refresh],
  );

  const savePromptNotes = useCallback((notes: PromptNotes) => {
    return legacyFetch("/api/settings/prompt-notes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(notes),
    }).then((r) => parseJsonOrThrow(r, "save failed"));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <CodeRequestsTab
      repos={repos}
      requests={requests}
      dispatchTarget={dispatchTarget}
      templates={templates}
      loading={loading}
      promptNotes={promptNotes}
      onDispatch={dispatchCodeRequest}
      onSaveTemplate={savePromptTemplate}
      onSavePromptNotes={savePromptNotes}
      onRefresh={() => refresh()}
    />
  );
}

export default CodeRequestsPage;
