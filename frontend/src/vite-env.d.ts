/// <reference types="vite/client" />

// Provides the ambient `ImportMeta.env` typing (ImportMetaEnv) so that
// `import.meta.env.*` access typechecks under `tsc`. Without this reference
// Vite's client types are not pulled in and `ImportMeta` has no `env` member.
