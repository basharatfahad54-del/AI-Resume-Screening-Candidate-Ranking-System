/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base URL of the API. Leave empty when the frontend and backend share an
   * origin (the nginx setup in production, and the dev proxy locally).
   */
  readonly VITE_API_URL?: string
  /** Dev-only override for the proxy target in vite.config.ts. */
  readonly VITE_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}