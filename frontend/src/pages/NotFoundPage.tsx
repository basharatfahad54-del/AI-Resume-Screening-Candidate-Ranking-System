import { Link } from 'react-router-dom'

import { EmptyState } from '@/components/Feedback'

export default function NotFoundPage() {
  return (
    <div className="grid min-h-screen place-items-center bg-slate-50 px-6">
      <div className="card w-full max-w-md p-6">
        <EmptyState
          title="Page not found"
          description="That URL does not match any screen in TalentMatch AI."
          action={
            <Link to="/" className="btn-primary btn-sm">
              Back to dashboard
            </Link>
          }
        />
      </div>
    </div>
  )
}