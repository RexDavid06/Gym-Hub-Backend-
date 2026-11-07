import type { paths } from './schema'

type ApiMethod = 'get' | 'post' | 'put' | 'patch' | 'delete'
type ApiPath = keyof paths & string
type PathsFor<M extends ApiMethod> = {
  [P in ApiPath]: paths[P] extends Record<M, unknown> ? P : never
}[ApiPath]
type Operation<P extends ApiPath, M extends ApiMethod> = paths[P] extends Record<M, infer O>
  ? O
  : never
type JsonBody<O> = O extends { requestBody?: { content?: { 'application/json'?: infer B } } }
  ? B extends undefined
    ? never
    : RequestBody<NonNullable<B>>
  : never

/**
 * drf-spectacular sometimes reuses a response resource as the request body,
 * which drags read-only fields the API ignores (id, created_at, updated_at)
 * into the type. Those become optional here; required fields on explicitly
 * generated request schemas (invites, logins, payments) are untouched.
 */
type WritableArrayElement<U> = U extends { readonly id: number }
  ? Omit<U, 'id'> & { id?: number }
  : U
type WritableArrays<B> = {
  [K in keyof B]: NonNullable<B[K]> extends readonly (infer U)[]
    ? WritableArrayElement<U>[]
    : B[K]
}
type RequestBody<B> = B extends { readonly id: number } ? Partial<WritableArrays<B>> : B
type Query<O> = O extends { parameters: { query?: infer Q } } ? Q : never
type SuccessResponse<O> = O extends { responses: infer R }
  ? {
      [S in keyof R]: R[S] extends { content: { 'application/json': infer C } } ? C : undefined
    }[keyof R]
  : never

type ApiOptions<O> = {
  body?: JsonBody<O>
  query?: Query<O>
  skipAuthRefresh?: boolean
}

export type ApiClientConfig = {
  baseUrl: string
  getAccessToken?: () => string | null
  refreshAccessToken?: () => Promise<string | null>
}

export class ApiError extends Error {
  status: number
  data: unknown

  constructor(message: string, status: number, data?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

const NETWORK_ERROR_MESSAGE =
  'Cannot reach Gym Hub right now. Check your internet connection and try again.'

function buildQuery(query: unknown) {
  if (!query || typeof query !== 'object') return ''
  const params: string[] = []
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    }
  }
  return params.length ? `?${params.join('&')}` : ''
}

async function readErrorMessage(response: Response) {
  try {
    const payload: unknown = await response.json()
    if (payload && typeof payload === 'object') {
      const record = payload as Record<string, unknown>
      if (typeof record.detail === 'string') return record.detail
      for (const value of Object.values(record)) {
        if (typeof value === 'string') return value
        if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
      }
    }
  } catch {
    // Fall back to the HTTP status when the response body is not JSON.
  }
  return `Request failed (${response.status})`
}

export function createApiClient(config: ApiClientConfig) {
  const baseUrl = config.baseUrl.replace(/\/$/, '')
  let refreshInFlight: Promise<string | null> | null = null

  function buildApiPath<P extends ApiPath>(
    template: P,
    params: Record<string, string | number>,
  ): P {
    return template.replace(/\{([^}]+)\}/g, (_, key: string) =>
      encodeURIComponent(String(params[key])),
    ) as P
  }

  async function requestWithToken<P extends PathsFor<M>, M extends ApiMethod>(
    path: P,
    method: M,
    options: ApiOptions<Operation<P, M>>,
  ) {
    const token = config.getAccessToken?.() ?? null
    try {
      return await fetch(`${baseUrl}${path}${buildQuery(options.query)}`, {
        method: method.toUpperCase(),
        headers: {
          ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
      })
    } catch {
      throw new ApiError(NETWORK_ERROR_MESSAGE, 0)
    }
  }

  async function apiFetch<P extends PathsFor<M>, M extends ApiMethod = 'get'>(
    path: P,
    method: M,
    options: ApiOptions<Operation<P, M>> = {},
  ): Promise<SuccessResponse<Operation<P, M>>> {
    let response = await requestWithToken(path, method, options)

    const refresh = config.refreshAccessToken
    if (response.status === 401 && !options.skipAuthRefresh && refresh) {
      refreshInFlight ??= refresh().finally(() => {
        refreshInFlight = null
      })
      const freshToken = await refreshInFlight
      if (freshToken) {
        response = await requestWithToken(path, method, { ...options, skipAuthRefresh: true })
      }
    }

    if (!response.ok) {
      let data: unknown
      try {
        data = await response.clone().json()
      } catch {
        data = undefined
      }
      throw new ApiError(await readErrorMessage(response), response.status, data)
    }

    if (response.status === 204) return undefined as SuccessResponse<Operation<P, M>>
    return (await response.json()) as SuccessResponse<Operation<P, M>>
  }

  return { apiFetch, buildApiPath }
}

export type ApiClient = ReturnType<typeof createApiClient>
