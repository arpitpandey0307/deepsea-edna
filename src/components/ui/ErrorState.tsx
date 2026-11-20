import { AlertTriangle, RefreshCw } from 'lucide-react';
import { cn, getErrorBannerMessage } from '@/lib/utils';

interface ErrorStateProps {
  title?: string;
  error?: unknown;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = 'Failed to load data',
  error,
  message,
  onRetry,
  className,
}: ErrorStateProps) {
  const detail = message || getErrorBannerMessage(error);

  return (
    <div
      role="alert"
      className={cn(
        'rounded-xl border border-rose-200 dark:border-rose-900/70 bg-rose-50/70 dark:bg-rose-950/25 p-5',
        className
      )}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-rose-600 dark:text-rose-400 shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold text-rose-900 dark:text-rose-200">{title}</h4>
          <p className="mt-1 text-xs text-rose-700 dark:text-rose-300/90 leading-relaxed break-words">
            {detail}
          </p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-rose-300 dark:border-rose-800 bg-white dark:bg-rose-950/60 px-3 py-1.5 text-xs font-medium text-rose-800 dark:text-rose-200 hover:bg-rose-100 dark:hover:bg-rose-900/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Retry request
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
