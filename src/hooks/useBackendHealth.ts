import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAuthStore } from '@/store/authStore';

export function useBackendHealth() {
  const apiUrl = useAuthStore((s) => s.apiBaseUrlOverride);

  return useQuery({
    queryKey: ['backend-health', apiUrl],
    queryFn: () => api.getHealth(),
    refetchInterval: 15000,
    retry: 1,
  });
}
