import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'

const jsonResponse = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )

describe('App', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('validates identity fields before signing in', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText(/name is required/i)).toBeInTheDocument()
    expect(screen.getByText(/valid email/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows an empty project list after sign in', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() =>
        jsonResponse({
          user: { id: 'user-1', name: 'Mira', email: 'mira@example.com', created_at: '2026-01-01' },
          token: 'session-token',
        }),
      )
      .mockImplementationOnce(() => jsonResponse([]))
    render(<App />)

    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: 'Mira' } })
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'mira@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument()
    expect(sessionStorage.getItem('gradionSession')).toBe('session-token')
  })

  it('creates a pasted-text project and opens its detail', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() =>
        jsonResponse({
          user: { id: 'user-1', name: 'Mira', email: 'mira@example.com', created_at: '2026-01-01' },
          token: 'session-token',
        }),
      )
      .mockImplementationOnce(() => jsonResponse([]))
      .mockImplementationOnce(() =>
        jsonResponse(
          {
            id: 'project-1',
            title: 'River Story',
            created_at: '2026-01-02T10:00:00Z',
            completed_stage: 'CREATED',
            book_text: 'Once beside the river...',
          },
          201,
        ),
      )
    render(<App />)
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: 'Mira' } })
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'mira@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    await screen.findByText(/no projects yet/i)

    fireEvent.click(screen.getByRole('button', { name: /new project/i }))
    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: 'River Story' },
    })
    fireEvent.change(screen.getByLabelText(/paste book text/i), {
      target: { value: 'Once beside the river...' },
    })
    fireEvent.click(screen.getByRole('button', { name: /create project/i }))

    expect(await screen.findByRole('heading', { name: 'River Story' })).toBeInTheDocument()
    expect(screen.getByText('Once beside the river...')).toBeInTheDocument()
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(3))
  })
})
