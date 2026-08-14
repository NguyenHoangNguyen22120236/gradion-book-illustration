const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

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
