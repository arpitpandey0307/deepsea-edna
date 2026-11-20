import type { ComponentType } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { OAuthProvider } from '@/types/api';

function GoogleLogo() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <path fill="#4285F4" d="M23.5 12.27c0-.82-.07-1.6-.2-2.36H12v4.47h6.45a5.52 5.52 0 0 1-2.4 3.62v3h3.88c2.27-2.09 3.57-5.17 3.57-8.73z" />
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.9l-3.88-3c-1.07.72-2.45 1.15-4.06 1.15-3.12 0-5.77-2.1-6.71-4.94H1.28v3.1A12 12 0 0 0 12 24z" />
      <path fill="#FBBC05" d="M5.29 14.31A7.2 7.2 0 0 1 4.91 12c0-.8.14-1.58.38-2.31v-3.1H1.28A12 12 0 0 0 0 12c0 1.94.46 3.77 1.28 5.41l4.01-3.1z" />
      <path fill="#EA4335" d="M12 4.75c1.76 0 3.34.61 4.59 1.8l3.44-3.44A11.97 11.97 0 0 0 12 0 12 12 0 0 0 1.28 6.59l4.01 3.1C6.23 6.85 8.88 4.75 12 4.75z" />
    </svg>
  );
}

function GitHubLogo() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 fill-current" aria-hidden="true">
      <path d="M12 .3a12 12 0 0 0-3.8 23.38c.6.12.83-.26.83-.57L9 21.07c-3.34.72-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.08-.74.09-.73.09-.73 1.2.09 1.83 1.24 1.83 1.24 1.07 1.83 2.81 1.3 3.5 1 .1-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.14-.3-.54-1.52.1-3.18 0 0 1-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.28-1.55 3.29-1.23 3.29-1.23.64 1.66.24 2.88.12 3.18a4.65 4.65 0 0 1 1.23 3.22c0 4.61-2.8 5.63-5.48 5.92.42.36.81 1.1.81 2.22l-.01 3.29c0 .31.2.69.82.57A12 12 0 0 0 12 .3" />
    </svg>
  );
}

const PROVIDERS: Array<{ id: OAuthProvider; label: string; Logo: ComponentType }> = [
  { id: 'google', label: 'Continue with Google', Logo: GoogleLogo },
  { id: 'github', label: 'Continue with GitHub', Logo: GitHubLogo },
];

/** Google/GitHub sign-in buttons; only providers configured on the server are shown */
export function OAuthButtons() {
  const providersQ = useQuery({
    queryKey: ['auth-providers'],
    queryFn: () => api.getAuthProviders(),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const enabled = PROVIDERS.filter((p) => providersQ.data?.[p.id]);
  if (enabled.length === 0) return null;

  return (
    <div className="space-y-2.5">
      {enabled.map(({ id, label, Logo }) => (
        <a
          key={id}
          href={api.oauthLoginUrl(id)}
          className="w-full inline-flex items-center justify-center gap-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-[#09101D] hover:bg-slate-50 dark:hover:bg-slate-800/60 px-4 py-2.5 text-sm font-medium text-slate-800 dark:text-slate-100 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
        >
          <Logo />
          {label}
        </a>
      ))}
      <div className="flex items-center gap-3 pt-1.5 text-[11px] uppercase tracking-wide text-slate-400">
        <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
        or use your email
        <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
      </div>
    </div>
  );
}
