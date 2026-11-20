import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { RequireAuth } from '@/components/auth/RequireAuth';
import { AppShell } from '@/components/layout/AppShell';
import { ToastContainer } from '@/components/ui/Toast';
import { lazy } from 'react';
import { LoginPage } from '@/pages/LoginPage';
import { OAuthCallbackPage } from '@/pages/OAuthCallbackPage';

// Pages load on first visit, so the login screen doesn't wait for charts and admin views
const DashboardPage = lazy(() => import('@/pages/DashboardPage').then((m) => ({ default: m.DashboardPage })));
const ClassifyPage = lazy(() => import('@/pages/ClassifyPage').then((m) => ({ default: m.ClassifyPage })));
const TaxaExplorerPage = lazy(() => import('@/pages/TaxaExplorerPage').then((m) => ({ default: m.TaxaExplorerPage })));
const ModelsPage = lazy(() => import('@/pages/ModelsPage').then((m) => ({ default: m.ModelsPage })));
const ExperimentsPage = lazy(() => import('@/pages/ExperimentsPage').then((m) => ({ default: m.ExperimentsPage })));
const MonitoringPage = lazy(() => import('@/pages/MonitoringPage').then((m) => ({ default: m.MonitoringPage })));
const AdminPage = lazy(() => import('@/pages/AdminPage').then((m) => ({ default: m.AdminPage })));
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })));
const HistoryPage = lazy(() => import('@/pages/HistoryPage').then((m) => ({ default: m.HistoryPage })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/oauth/callback" element={<OAuthCallbackPage />} />

            <Route
              path="/history"
              element={
                <RequireAuth>
                  <AppShell>
                    <HistoryPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/"
              element={
                <RequireAuth>
                  <AppShell>
                    <DashboardPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/classify"
              element={
                <RequireAuth>
                  <AppShell>
                    <ClassifyPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/taxa"
              element={
                <RequireAuth>
                  <AppShell>
                    <TaxaExplorerPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/models"
              element={
                <RequireAuth roles={['admin', 'scientist', 'viewer']}>
                  <AppShell>
                    <ModelsPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/experiments"
              element={
                <RequireAuth roles={['admin', 'scientist', 'viewer']}>
                  <AppShell>
                    <ExperimentsPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/monitoring"
              element={
                <RequireAuth roles={['admin']}>
                  <AppShell>
                    <MonitoringPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/admin"
              element={
                <RequireAuth roles={['admin']}>
                  <AppShell>
                    <AdminPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route
              path="/settings"
              element={
                <RequireAuth>
                  <AppShell>
                    <SettingsPage />
                  </AppShell>
                </RequireAuth>
              }
            />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
        <ToastContainer />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

export default App;
