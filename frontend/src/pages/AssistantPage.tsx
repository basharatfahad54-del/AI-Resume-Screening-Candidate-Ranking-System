import { useEffect, useRef, useState } from 'react'
import { Bot, Send, Sparkles } from 'lucide-react'

import { assistantApi } from '@/api'
import { jobsApi } from '@/api/jobs'
import type { AssistantResponse } from '@/api/types'
import { Button } from '@/components/Form'
import { PageHeader } from '@/components/Feedback'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'

interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  payload?: AssistantResponse
}

const SUGGESTIONS = [
  'Which job has the most candidates?',
  'Top 5 candidates for the newest job',
  'Which skills are most commonly missing across active jobs?',
  'How many candidates are shortlisted?',
]

let messageId = 0

export default function AssistantPage() {
  const toast = useToast()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [jobId, setJobId] = useState<string>('')
  const [isSending, setIsSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const jobs = useAsync(() => jobsApi.list({ page_size: 100 }))
  const status = useAsync(() => assistantApi.status())

  // The backend answers from structured queries over its own database; it is not
  // a general chat model, so the UI states that plainly instead of pretending.
  const mode = (status.data?.mode as string | undefined) ?? null

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || isSending) return

    const userMessage: Message = { id: ++messageId, role: 'user', text: trimmed }
    setMessages((current) => [...current, userMessage])
    setInput('')
    setIsSending(true)

    const conversationId = [...messages].reverse().find((m) => m.payload?.conversation_id)?.payload
      ?.conversation_id

    try {
      const response = await assistantApi.query({
        message: trimmed,
        job_id: jobId ? Number(jobId) : null,
        conversation_id: conversationId ?? null,
      })
      setMessages((current) => [
        ...current,
        { id: ++messageId, role: 'assistant', text: response.answer, payload: response },
      ])
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : 'The assistant could not answer that.')
    } finally {
      setIsSending(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Assistant"
        description={
          mode === 'llm'
            ? 'Ask questions about your jobs and candidates in plain language.'
            : 'Ask structured questions about your jobs and candidates. Answers come from queries over this workspace’s own data.'
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card flex h-[70vh] flex-col lg:col-span-2">
          <div className="card-header">
            <h2 className="card-title flex items-center gap-2">
              <Bot className="h-4 w-4 text-brand-600" aria-hidden="true" />
              Conversation
            </h2>
            {messages.length > 0 ? (
              <Button variant="ghost" size="sm" onClick={() => setMessages([])}>
                Clear
              </Button>
            ) : null}
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto p-5">
            {messages.length === 0 ? (
              <div className="space-y-3">
                <p className="text-sm text-slate-500">Try one of these:</p>
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className="badge border border-slate-200 bg-white text-slate-700 transition hover:border-brand-400 hover:text-brand-700"
                      onClick={() => void send(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((message) => (
                <div
                  key={message.id}
                  className={message.role === 'user' ? 'flex justify-end' : 'flex justify-start'}
                >
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
                      message.role === 'user'
                        ? 'bg-brand-600 text-white'
                        : 'border border-slate-200 bg-white text-slate-800'
                    }`}
                  >
                    <p className="whitespace-pre-wrap leading-relaxed">{message.text}</p>

                    {message.payload?.tables.map((table, index) => (
                      <div key={index} className="mt-3 overflow-x-auto rounded-lg border border-slate-200">
                        {table.title ? (
                          <p className="border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-600">
                            {table.title}
                          </p>
                        ) : null}
                        <table className="min-w-full text-xs">
                          <thead>
                            <tr>
                              {table.columns.map((column) => (
                                <th key={column} scope="col" className="px-3 py-1.5 text-left font-semibold text-slate-600">
                                  {column}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {table.rows.map((row, rowIndex) => (
                              <tr key={rowIndex} className="border-t border-slate-100">
                                {row.map((cell, cellIndex) => (
                                  <td key={cellIndex} className="px-3 py-1.5 text-slate-700">
                                    {String(cell ?? '—')}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}

                    {message.payload && message.payload.suggestions.length > 0 ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {message.payload.suggestions.map((suggestion) => (
                          <button
                            key={suggestion}
                            type="button"
                            className="badge border border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100"
                            onClick={() => void send(suggestion)}
                          >
                            {suggestion}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))
            )}
            <div ref={bottomRef} />
          </div>

          <form
            className="flex items-end gap-2 border-t border-slate-100 p-4"
            onSubmit={(event) => {
              event.preventDefault()
              void send(input)
            }}
          >
            <div className="flex-1">
              <label className="sr-only" htmlFor="assistant-input">
                Ask a question
              </label>
              <textarea
                id="assistant-input"
                className="input min-h-[2.75rem] resize-none"
                rows={1}
                placeholder="Ask about jobs, candidates, or skills…"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void send(input)
                  }
                }}
              />
            </div>
            <Button type="submit" loading={isSending} disabled={!input.trim()}>
              <Send className="h-4 w-4" aria-hidden="true" />
              <span className="sr-only">Send</span>
            </Button>
          </form>
        </section>

        <aside className="space-y-6">
          <div className="card p-5">
            <h2 className="card-title">Scope</h2>
            <p className="mt-2 text-sm text-slate-600">
              Restrict answers to a single job, or leave unset to query everything.
            </p>
            <label className="sr-only" htmlFor="assistant-job">
              Job
            </label>
            <select
              id="assistant-job"
              className="input mt-3"
              value={jobId}
              onChange={(event) => setJobId(event.target.value)}
            >
              <option value="">All jobs</option>
              {jobs.data?.results.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title}
                </option>
              ))}
            </select>
          </div>

          <div className="card p-5">
            <h2 className="card-title flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-brand-600" aria-hidden="true" />
              Capabilities
            </h2>
            {status.isLoading ? (
              <p className="mt-2 text-sm text-slate-500">Checking…</p>
            ) : (
              <ul className="mt-3 space-y-2 text-sm text-slate-600">
                <li>
                  <span className="font-medium text-slate-800">Mode:</span>{' '}
                  {mode ?? 'structured_query'}
                </li>
                <li>
                  <span className="font-medium text-slate-800">Embedding provider:</span>{' '}
                  {String(status.data?.embedding_provider ?? 'unknown')}
                </li>
                <li>
                  <span className="font-medium text-slate-800">LLM provider:</span>{' '}
                  {String(status.data?.llm_provider ?? 'none (rule-based answers)')}
                </li>
              </ul>
            )}
            <p className="mt-4 text-xs leading-relaxed text-slate-500">
              The assistant only reads data already in this workspace. It cannot take actions, contact
              candidates, or make hiring decisions, and every answer is grounded in the stored records.
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}