import type { Project } from "./types";

export function resolveApiBase(configuredBase?: string): string {
  const value = configuredBase?.trim();
  if (!value) return "/api";

  // A browser API base must never be a local Windows filesystem path. This can
  // happen when Git Bash path-converts VITE_API_BASE_URL=/api for npm.exe.
  if (/^(?:file:\/\/\/|\/?[a-z]:[\\/])/i.test(value)) return "/api";

  return value.replace(/\/$/, "");
}

const API_BASE = resolveApiBase(import.meta.env.VITE_API_BASE_URL);

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.detail ?? "Request failed", response.status);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export async function authenticatedImage(url: string, token: string) {
  return authenticatedMedia(url, token, "Generated image could not be loaded");
}

export async function authenticatedMedia(
  url: string,
  token: string,
  errorMessage = "Protected media could not be loaded",
) {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(errorMessage);
  return response.blob();
}

export type ProjectStateStreamHandlers = {
  onProjectState: (project: Project) => void;
  onDisconnect: (error: Error) => void;
};

export function openProjectStateStream(
  projectId: string,
  token: string,
  handlers: ProjectStateStreamHandlers,
): () => void {
  let cancelled = false;
  let controller: AbortController | null = null;
  let retryTimer: number | null = null;
  let finishRetryWait: (() => void) | null = null;
  let fallbackReported = false;

  const waitBeforeReconnect = () =>
    new Promise<void>((resolve) => {
      finishRetryWait = resolve;
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        finishRetryWait = null;
        resolve();
      }, 1000);
    });

  const connect = async () => {
    controller = new AbortController();
    const response = await fetch(
      `${API_BASE}/projects/${encodeURIComponent(projectId)}/events`,
      {
        headers: {
          Accept: "text/event-stream",
          Authorization: `Bearer ${token}`,
        },
        cache: "no-store",
        signal: controller.signal,
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new ApiError(body?.detail ?? "Project update stream failed", response.status);
    }
    if (!response.body) throw new Error("Project update stream is unavailable");
    await consumeProjectStateEvents(response.body, (state) => {
      if (!cancelled) {
        fallbackReported = false;
        handlers.onProjectState(state);
      }
    });
    if (!cancelled) throw new Error("Project update stream disconnected");
  };

  const run = async () => {
    while (!cancelled) {
      try {
        await connect();
      } catch (reason) {
        if (cancelled || (reason instanceof DOMException && reason.name === "AbortError")) {
          return;
        }
        if (!fallbackReported) {
          fallbackReported = true;
          handlers.onDisconnect(
            reason instanceof Error ? reason : new Error("Project update stream disconnected"),
          );
        }
        await waitBeforeReconnect();
      }
    }
  };

  void run();
  return () => {
    cancelled = true;
    controller?.abort();
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    finishRetryWait?.();
    retryTimer = null;
    finishRetryWait = null;
  };
}

async function consumeProjectStateEvents(
  body: ReadableStream<Uint8Array>,
  onProjectState: (project: Project) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        parseProjectStateFrame(frame, onProjectState);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

function parseProjectStateFrame(
  frame: string,
  onProjectState: (project: Project) => void,
) {
  if (!frame || frame.startsWith(":")) return;
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (event === "project-state" && data.length) {
    onProjectState(JSON.parse(data.join("\n")) as Project);
  }
}
