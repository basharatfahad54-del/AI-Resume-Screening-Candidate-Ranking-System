import { useEffect, useState } from 'react'

/** Delay a rapidly changing value (a search box) before it is sent to the API. */
export function useDebounced<T>(value: T, delayMs = 350): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [value, delayMs])

  return debounced
}

/** Persist a piece of UI state (a filter panel, a tab) across reloads. */
export function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void] {
  const [stored, setStored] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key)
      return raw === null ? initialValue : (JSON.parse(raw) as T)
    } catch {
      return initialValue
    }
  })

  const setValue = (value: T) => {
    setStored(value)
    try {
      window.localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // Private-browsing quota errors must not break the screen.
    }
  }

  return [stored, setValue]
}