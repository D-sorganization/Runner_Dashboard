import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import {
  FeatureRequestsTab,
  type FeatureDispatchPayload,
  type FeatureRepo,
  type FeatureRequestRecord,
  type PromptNotes,
  type PromptTemplate,
} from "./FeatureRequests";

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

interface ReposPayload {
  repos?: FeatureRepo[];
}

interface RequestsPayload {
  requests?: FeatureRequestRecord[];
}

interface TemplatesPayload {
  templates?: PromptTemplate[];
  promptNotes?: PromptNotes;
}

function normalizeReposPayload(payload: unknown): FeatureRepo[] {
  if (Array.isArray(payload)) return payload as FeatureRepo[];
  if (payload && typeof payload === "object") {
    const repos = (payload as ReposPayload).repos;
    if (Array.isArray(repos)) return repos;
  }
  return [];
}

function normalizeRequestsPayload(payload: unknown): FeatureRequestRecord[] {
  if (Array.isArray(payload)) return payload as FeatureRequestRecord[];
  if (payload && typeof payload === "object") {
    const requests = (payload as RequestsPayload).requests;
    if (Array.isArray(requests)) return requests;
  }
  return [];
}

function normalizeTemplatesPayload(payload: unknown): TemplatesPayload {
  if (!payload || typeof payload !== "object") {
    return { templates: [], promptNotes: { notes: "", enabled: true } };
  }
  const data = payload as TemplatesPayload;
  return {
    templates: Array.isArray(data.templates) ? data.templates : [],
    promptNotes:
      data.promptNotes && typeof data.promptNotes === "object"
        ? data.promptNotes
        : { notes: "", enabled: true },
  };
}

function readJsonOrThrow(response: Response, fallback: string): Promise<unknown> {
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

export function FeatureRequestsPage(): React.ReactElement {
  const [repos, setRepos] = useState<FeatureRepo[]>([]);
  const [requests, setRequests] = useState<FeatureRequestRecord[]>([]);
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [promptNotes, setPromptNotes] = useState<PromptNotes>({
    notes: "",
    enabled: true,
  });
  const [loading, setLoading] = useState(true);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    Promise.all([
      legacyFetch("/api/repos", { signal })
        .then((r) => readJsonOrThrow(r, "repos failed"))
        .catch(() => []),
      legacyFetch("/api/feature-requests", { signal })
        .then((r) => readJsonOrThrow(r, "feature requests failed"))
        .catch(() => ({ requests: [] })),
      legacyFetch("/api/feature-requests/templates", { signal })
        .then((r) => readJsonOrThrow(r, "templates failed"))
        .catch(() => ({
          templates: [],
          promptNotes: { notes: "", enabled: true },
        })),
    ])
      .then(([reposPayload, requestsPayload, templatesPayload]) => {
        setRepos(normalizeReposPayload(reposPayload));
        setRequests(normalizeRequestsPayload(requestsPayload));
        const normalizedTemplates = normalizeTemplatesPayload(templatesPayload);
        setTemplates(normalizedTemplates.templates || []);
        setPromptNotes(
          normalizedTemplates.promptNotes || { notes: "", enabled: true },
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const dispatchFeatureRequest = useCallback(
    (payload: FeatureDispatchPayload) => {
      return legacyFetch("/api/feature-requests/dispatch", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify(payload),
      }).then((r) => readJsonOrThrow(r, "dispatch failed"));
    },
    [],
  );

  const savePromptTemplate = useCallback(
    (template: PromptTemplate) => {
      return legacyFetch("/api/prompt-templates", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify(template),
      })
        .then((r) => readJsonOrThrow(r, "save failed"))
        .then((data) => {
          refresh();
          return data;
        });
    },
    [refresh],
  );

  const updatePromptNotes = useCallback((notes: PromptNotes) => {
    return legacyFetch("/api/settings/prompt-notes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(notes),
    }).then((r) => readJsonOrThrow(r, "save failed"));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <FeatureRequestsTab
      repos={repos}
      requests={requests}
      templates={templates}
      loading={loading}
      promptNotes={promptNotes}
      onDispatch={dispatchFeatureRequest}
      onSaveTemplate={savePromptTemplate}
      onSavePromptNotes={updatePromptNotes}
      onRefresh={() => refresh()}
    />
  );
}

export default FeatureRequestsPage;
