import { useCallback, useEffect, useRef, useState } from 'react'

interface AsyncState<T> {
  data: T | null
  error: Error | null
  isLoading: boolean
}

export interface AsyncResult<T> extends AsyncState<T> {
  /** Re-run the fetcher. `execute` is awaited so callers can chain on it. */
  execute: (...args: unknown[]) => Promise<T | null>
  setData: (data: T | null) => void
  isEmpty: boolean
}

/**
 * Small data-loading helper.
 *
 * It deliberately ignores results from a request that was superseded, which is
 * what keeps a fast typist from seeing the first search result replace the
 * second one in the table below the input.
 */
export function useAsync<T>(
  fetcher: (...args: never[]) => Promise<T>,
  options: { immediate?: boolean; initialData?: T | null } = {},
): AsyncResult<T> {
  const { immediate = true, initialData = null } = options
  const [state, setState] = useState<AsyncState<T>>({
    data: initialData,
    error: null,
    isLoading: immediate,
  })
  const requestId = useRef(0)
  const mounted = useRef(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const execute = useCallback(async (...args: unknown[]) => {
    const id = ++requestId.current
    // Snapshot the data we are about to supersede so a failed refresh keeps
    // showing the last good result instead of blanking the screen.
    let previous: T | null = null
    setState((current) => {
      previous = current.data
      return { ...current, isLoading: true, error: null }
    })
    try {
      const result = await fetcherRef.current(...(args as never[]))
      if (!mounted.current || id !== requestId.current) return null
      setState({ data: result, error: null, isLoading: false })
      return result
    } catch (error) {
      if (!mounted.current || id !== requestId.current) return null
      setState({
        data: previous,
        error: error instanceof Error ? error : new Error(String(error)),
        isLoading: false,
      })
      return null
    }
  }, [])

  useEffect(() => {
    if (immediate) {
      void execute()
    }
  }, [execute, immediate])

  const setData = useCallback((data: T | null) => {
    setState((current) => ({ ...current, data }))
  }, [])

  return {
    ...state,
    execute,
    setData,
    isEmpty: state.data !== null && !state.isLoading && state.error === null,
  }
}