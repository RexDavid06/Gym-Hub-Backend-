import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, createApiClient } from '../src/client'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function emptyResponse(status: number) {
  return new Response(null, { status })
}

function stubFetch() {
  const fetchSpy = vi.fn()
  vi.stubGlobal('fetch', fetchSpy)
  return fetchSpy
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createApiClient', () => {
  it('encodes path parameters', () => {
    const client = createApiClient({ baseUrl: 'http://api.test' })

    expect(client.buildApiPath('/api/users/{user_id}/branch/', { user_id: 'a b/c' })).toBe(
      '/api/users/a%20b%2Fc/branch/',
    )
  })

  it('sends query parameters, omits empty values, and attaches the access token', async () => {
    const fetchSpy = stubFetch().mockResolvedValue(jsonResponse([]))
    const client = createApiClient({
      baseUrl: 'http://api.test/',
      getAccessToken: () => 'token-1',
    })

    await client.apiFetch('/api/users/', 'get', {
      query: { role: 'member', ignored: '' } as never,
    })

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('http://api.test/api/users/?role=member')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-1')
  })

  it('serializes JSON bodies and sets the content type only when a body exists', async () => {
    const fetchSpy = stubFetch().mockResolvedValue(jsonResponse({ access: 'a', refresh: 'r' }))
    const client = createApiClient({ baseUrl: 'http://api.test' })

    await client.apiFetch('/api/token/', 'post', {
      body: { phone: '08012345678', password: 'secret' },
      skipAuthRefresh: true,
    })

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ phone: '08012345678', password: 'secret' }))
  })

  it('resolves 204 responses to undefined', async () => {
    stubFetch().mockResolvedValue(emptyResponse(204))
    const client = createApiClient({ baseUrl: 'http://api.test' })
    const path = client.buildApiPath('/api/workout-plans/{id}/', { id: 7 })

    const result = await client.apiFetch(path, 'delete')

    expect(result).toBeUndefined()
  })

  it('parses detail messages into ApiError', async () => {
    stubFetch().mockResolvedValue(jsonResponse({ detail: 'Not found.' }, 404))
    const client = createApiClient({ baseUrl: 'http://api.test' })

    const error = await client.apiFetch('/api/me/', 'get').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, message: 'Not found.' })
  })

  it('falls back to field errors and then the HTTP status', async () => {
    const fetchSpy = stubFetch()
    const client = createApiClient({ baseUrl: 'http://api.test' })

    fetchSpy.mockResolvedValueOnce(jsonResponse({ name: ['This field is required.'] }, 400))
    const fieldError = await client.apiFetch('/api/me/', 'get').catch((caught: unknown) => caught)
    expect(fieldError).toMatchObject({ status: 400, message: 'This field is required.' })

    fetchSpy.mockResolvedValueOnce(jsonResponse({}, 500))
    const fallback = await client.apiFetch('/api/me/', 'get').catch((caught: unknown) => caught)
    expect(fallback).toMatchObject({ status: 500, message: 'Request failed (500)' })
  })

  it('maps network failures to an ApiError with status 0', async () => {
    stubFetch().mockRejectedValue(new TypeError('Failed to fetch'))
    const client = createApiClient({ baseUrl: 'http://api.test' })

    const error = await client.apiFetch('/api/me/', 'get').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 0,
      message: 'Cannot reach Gym Hub right now. Check your internet connection and try again.',
    })
  })

  it('refreshes once after a 401 and retries with the new token', async () => {
    const fetchSpy = stubFetch()
      .mockResolvedValueOnce(emptyResponse(401))
      .mockResolvedValueOnce(jsonResponse({ id: 1 }, 200))
    const refreshAccessToken = vi.fn(async () => 'fresh-token')
    let accessToken = 'expired-token'
    const client = createApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: () => accessToken,
      refreshAccessToken: async () => {
        accessToken = (await refreshAccessToken()) ?? accessToken
        return accessToken
      },
    })

    await client.apiFetch('/api/me/', 'get')

    expect(refreshAccessToken).toHaveBeenCalledTimes(1)
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    const [, retryInit] = fetchSpy.mock.calls[1] as [string, RequestInit]
    expect((retryInit.headers as Record<string, string>).Authorization).toBe('Bearer fresh-token')
  })

  it('deduplicates concurrent refresh attempts', async () => {
    const fetchSpy = stubFetch()
      .mockResolvedValueOnce(emptyResponse(401))
      .mockResolvedValueOnce(emptyResponse(401))
      .mockResolvedValueOnce(jsonResponse({ id: 1 }, 200))
      .mockResolvedValueOnce(jsonResponse({ id: 2 }, 200))
    let release: (token: string | null) => void = () => undefined
    const refreshAccessToken = vi.fn(
      () =>
        new Promise<string | null>((resolve) => {
          release = resolve
        }),
    )
    const client = createApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: () => 'expired-token',
      refreshAccessToken,
    })

    const first = client.apiFetch('/api/me/', 'get')
    const second = client.apiFetch('/api/me/', 'get')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(refreshAccessToken).toHaveBeenCalledTimes(1)
    release('fresh-token')
    await Promise.all([first, second])

    expect(fetchSpy).toHaveBeenCalledTimes(4)
  })

  it('does not refresh when skipAuthRefresh is set', async () => {
    const fetchSpy = stubFetch().mockResolvedValue(emptyResponse(401))
    const refreshAccessToken = vi.fn(async () => 'fresh-token')
    const client = createApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: () => 'expired-token',
      refreshAccessToken,
    })

    await client.apiFetch('/api/me/', 'get', { skipAuthRefresh: true }).catch(() => undefined)

    expect(refreshAccessToken).not.toHaveBeenCalled()
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })
})
