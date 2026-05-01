import { z } from "zod";

export const KNOWN_PROVIDERS = [
  "jules_api",
  "codex_cli",
  "claude_code_cli",
  "gemini_cli",
  "ollama",
  "cline",
] as const;

export const quickDispatchSchema = z.object({
  prompt: z
    .string()
    .min(10, "Prompt must be at least 10 characters")
    .max(10000, "Prompt must be at most 10,000 characters"),
  repo: z
    .string()
    .min(1, "Repository slug is required")
    .regex(
      /^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/,
      "Repo must match the format owner/repo"
    ),
  provider: z.enum(KNOWN_PROVIDERS, {
    message: "Please select a known provider",
  }),
});

export type QuickDispatchForm = z.infer<typeof quickDispatchSchema>;

export const credentialKeySchema = z.object({
  key: z
    .string()
    .min(1, "API key is required")
    .max(2000, "API key must be at most 2,000 characters"),
});

export type CredentialKeyForm = z.infer<typeof credentialKeySchema>;
