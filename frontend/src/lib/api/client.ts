import type { ApiError } from './types';

const BASE_URL = '';

// ── Auth token injection ──
// The auth store registers a getter here to avoid circular imports.
let _getToken: (() => string | null) | null = null;
let _refreshToken: (() => Promise<string | null>) | null = null;

export function setTokenProvider(
  getToken: () => string | null,
  refreshToken: () => Promise<string | null>,
): void {
  _getToken = getToken;
  _refreshToken = refreshToken;
}

// ── Error class ──

export class ApiClientError extends Error {
  constructor(
    public status: number,
    public apiError: ApiError,
  ) {
    super(apiError.error);
    this.name = 'ApiClientError';
  }
}

// ── Core request function ──

async function request<T>(
  path: string,
  options: RequestInit = {},
  isRetry = false,
): Promise<T> {
  const url = `${BASE_URL}${path}`;

  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  if (options.body && typeof options.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }

  // Inject Authorization header if available
  const token = _getToken?.();
  if (token && !headers['Authorization']) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });

  // Auto-refresh on 401 (skip for auth endpoints to avoid loops)
  if (response.status === 401 && !isRetry && _refreshToken && !path.startsWith('/api/v1/firesky/auth')) {
    const newToken = await _refreshToken();
    if (newToken) {
      return request<T>(path, options, true);
    }
  }

  if (!response.ok) {
    let apiError: ApiError;
    try {
      apiError = await response.json();
    } catch {
      apiError = { error: `HTTP ${response.status}: ${response.statusText}` };
    }
    throw new ApiClientError(response.status, apiError);
  }

  return response.json();
}

// ── HTTP method helpers ──

export function get<T>(path: string, params?: Record<string, string>, signal?: AbortSignal): Promise<T> {
  const query = params ? '?' + new URLSearchParams(params).toString() : '';
  return request<T>(`${path}${query}`, signal ? { signal } : undefined);
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function put<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

/**
 * Open an SSE connection and return the EventSource.
 */
export function openSSE(
  path: string,
  params: Record<string, string>,
  handlers: {
    onEvent: (eventName: string, data: unknown) => void;
    onError?: (error: Event) => void;
    onOpen?: () => void;
  },
): EventSource {
  const query = new URLSearchParams(params).toString();
  const url = `${BASE_URL}${path}?${query}`;
  const source = new EventSource(url);

  source.onopen = () => handlers.onOpen?.();

  source.onerror = (e) => {
    handlers.onError?.(e);
  };

  for (const eventName of ['progress', 'done', 'failed', 'cancelled']) {
    source.addEventListener(eventName, (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        handlers.onEvent(eventName, data);
      } catch (err) {
        console.error(`Failed to parse SSE event "${eventName}":`, err);
      }
    });
  }

  return source;
}
