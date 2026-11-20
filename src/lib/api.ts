import axios, {
  AxiosError,
  AxiosInstance,
  InternalAxiosRequestConfig,
} from 'axios';
import { useAuthStore } from '@/store/authStore';
import { TAXA_REFERENCE } from '@/data/taxaReference';
import type {
  AnalysisJobResponse,
  ApiErrorInfo,
  ApplicationMetricsResponse,
  AuthProvidersResponse,
  JobHistoryItem,
  MyStatsResponse,
  OAuthProvider,
  PaginatedResponse,
  PredictionHistoryItem,
  ServingModelInfo,
  UserUpdateRequest,
  BatchPredictResponse,
  CacheClearResponse,
  CacheStatsResponse,
  DashboardMetricsResponse,
  DetailedHealthResponse,
  Experiment,
  ExperimentResultsResponse,
  ExperimentsListResponse,
  ExportFormat,
  HealthResponse,
  ModelVersion,
  PermissionsResponse,
  PredictResponse,
  RefreshTokenResponse,
  RegisterRequest,
  SecurityStatsResponse,
  SystemHealthResponse,
  TokenResponse,
  UploadResponse,
  User,
  UsersListResponse,
  VersionComparisonResponse,
  VersionHistoryResponse,
  VersionListResponse,
} from '@/types/api';

interface RetryableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

let refreshPromise: Promise<string> | null = null;

function parseValidationDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const rec = item as Record<string, unknown>;
          const loc = Array.isArray(rec.loc) ? rec.loc.join('.') : '';
          const msg = typeof rec.msg === 'string' ? rec.msg : JSON.stringify(rec);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(item);
      })
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail);
  }
  return '';
}

export function normalizeApiError(error: unknown): ApiErrorInfo {
  if (axios.isAxiosError(error)) {
    const axiosErr = error as AxiosError<{ detail?: unknown; message?: string }>;
    const status = axiosErr.response?.status;
    const rawDetail = axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
    const detailText = parseValidationDetail(rawDetail);

    if (!axiosErr.response) {
      return {
        code: 'NETWORK',
        isNetworkError: true,
        message:
          'Unable to reach the DeepSea eDNA FastAPI backend. Check the configured API URL, CORS policy, or HTTPS mixed-content settings.',
        details: axiosErr.message,
      };
    }

    if (status === 401) {
      return {
        status,
        code: 'UNAUTHORIZED',
        isNetworkError: false,
        message: detailText || 'Authentication failed or session expired. Please sign in again.',
      };
    }

    if (status === 429) {
      return {
        status,
        code: 'RATE_LIMITED',
        isNetworkError: false,
        message: detailText || 'Too many attempts. Please wait a minute and try again.',
      };
    }

    if (status === 403) {
      return {
        status,
        code: 'FORBIDDEN',
        isNetworkError: false,
        message: detailText || 'You do not have permission to perform this action.',
      };
    }

    if (status === 413) {
      return {
        status,
        code: 'PAYLOAD_TOO_LARGE',
        isNetworkError: false,
        message: detailText || 'Uploaded file exceeds the server size limit (max 500 MB).',
      };
    }

    if (status === 422) {
      return {
        status,
        code: 'VALIDATION',
        isNetworkError: false,
        message: detailText ? `Validation error: ${detailText}` : 'Invalid request payload (422).',
      };
    }

    if (status === 404) {
      return {
        status,
        code: 'NOT_FOUND',
        isNetworkError: false,
        message: detailText || 'Requested resource or endpoint was not found (404).',
      };
    }

    if (status && status >= 500) {
      return {
        status,
        code: 'SERVER',
        isNetworkError: false,
        message: detailText || `Server error (${status}). Check FastAPI logs for details.`,
      };
    }

    return {
      status,
      code: 'UNKNOWN',
      isNetworkError: false,
      message: detailText || axiosErr.message || 'Request failed.',
    };
  }

  if (error instanceof Error) {
    return {
      code: 'UNKNOWN',
      isNetworkError: false,
      message: error.message,
    };
  }

  return {
    code: 'UNKNOWN',
    isNetworkError: false,
    message: 'Unexpected error occurred.',
  };
}

function createApiClient(): AxiosInstance {
  const client = axios.create({
    timeout: 30000,
  });

  client.interceptors.request.use((config) => {
    const store = useAuthStore.getState();
    config.baseURL = store.getEffectiveApiBaseUrl();

    if (store.accessToken) {
      config.headers.set('Authorization', `Bearer ${store.accessToken}`);
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => {
      const store = useAuthStore.getState();
      if (store.backendUnreachable) {
        store.setBackendStatus(false, null);
      }
      return response;
    },
    async (error: AxiosError) => {
      const store = useAuthStore.getState();

      if (!error.response) {
        store.setBackendStatus(
          true,
          `Cannot reach ${store.getEffectiveApiBaseUrl()} (${error.message || 'Network/CORS error'})`
        );
        return Promise.reject(normalizeApiError(error));
      }

      const originalConfig = error.config as RetryableConfig | undefined;
      const status = error.response.status;
      const reqUrl = originalConfig?.url ?? '';

      const isAuthEndpoint =
        reqUrl.includes('/auth/token') || reqUrl.includes('/auth/refresh') || reqUrl.includes('/auth/register');

      if (status === 401 && originalConfig && !originalConfig._retry && !isAuthEndpoint) {
        const currentRefreshToken = store.refreshToken;
        if (!currentRefreshToken) {
          store.clearAuth();
          if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
            window.location.assign('/login');
          }
          return Promise.reject(normalizeApiError(error));
        }

        originalConfig._retry = true;

        try {
          if (!refreshPromise) {
            refreshPromise = (async () => {
              const baseURL = store.getEffectiveApiBaseUrl();
              const res = await axios.post<RefreshTokenResponse>(
                `${baseURL}/auth/refresh`,
                { refresh_token: currentRefreshToken },
                {
                  params: { refresh_token: currentRefreshToken },
                  timeout: 15000,
                }
              );
              const nextAccess = res.data?.access_token;
              if (!nextAccess) {
                throw new Error('Refresh response missing access_token');
              }
              // Refresh tokens are single-use: keep the rotated one
              useAuthStore.getState().setTokens(nextAccess, res.data.refresh_token ?? currentRefreshToken);
              return nextAccess;
            })().finally(() => {
              refreshPromise = null;
            });
          }

          const newToken = await refreshPromise;
          originalConfig.headers.set('Authorization', `Bearer ${newToken}`);
          return client(originalConfig);
        } catch {
          useAuthStore.getState().clearAuth();
          if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
            window.location.assign('/login');
          }
          return Promise.reject(normalizeApiError(error));
        }
      }

      return Promise.reject(normalizeApiError(error));
    }
  );

  return client;
}

export const httpClient = createApiClient();

function normalizePredictResponse(raw: Record<string, unknown>): PredictResponse {
  const predictedClass =
    typeof raw.predicted_class === 'string'
      ? raw.predicted_class
      : typeof raw.taxon_name === 'string'
      ? raw.taxon_name
      : 'Unknown';

  const confidence = typeof raw.confidence === 'number' ? raw.confidence : 0;
  const rawAlts = Array.isArray(raw.alternatives)
    ? raw.alternatives
    : Array.isArray(raw.top_predictions)
    ? raw.top_predictions
    : [];

  const alternatives = rawAlts
    .map((item) => {
      const obj = (item ?? {}) as Record<string, unknown>;
      const cls =
        typeof obj.class === 'string'
          ? obj.class
          : typeof obj.taxon_name === 'string'
          ? obj.taxon_name
          : 'Unknown';
      const conf =
        typeof obj.confidence === 'number'
          ? obj.confidence
          : typeof obj.probability === 'number'
          ? obj.probability
          : 0;
      return { class: cls, confidence: conf };
    })
    .filter((a) => a.class !== 'Unknown');

  const rawProbs =
    raw.probabilities && typeof raw.probabilities === 'object'
      ? (raw.probabilities as Record<string, number>)
      : undefined;

  return {
    predicted_class: predictedClass,
    confidence,
    alternatives,
    probabilities: rawProbs,
    cached: Boolean(raw.cached),
    is_novel: Boolean(raw.is_novel),
    class_id: typeof raw.class_id === 'number' ? raw.class_id : undefined,
    rank: typeof raw.rank === 'string' ? raw.rank : undefined,
    lineage: Array.isArray(raw.lineage) ? (raw.lineage as string[]) : undefined,
  };
}

export function buildAllTaxaProbabilities(pred: PredictResponse): Array<{ taxon: string; probability: number; isTop: boolean }> {
  const probMap = new Map<string, number>();

  for (const t of TAXA_REFERENCE) {
    probMap.set(t.name, 0);
  }

  if (pred.probabilities) {
    for (const [k, v] of Object.entries(pred.probabilities)) {
      if (typeof v === 'number') {
        probMap.set(k, v <= 1 ? v * 100 : v);
      }
    }
  } else {
    const topProb = pred.confidence <= 1 ? pred.confidence * 100 : pred.confidence;
    probMap.set(pred.predicted_class, topProb);

    let used = topProb;
    for (const alt of pred.alternatives ?? []) {
      const p = alt.confidence <= 1 ? alt.confidence * 100 : alt.confidence;
      if (alt.class !== pred.predicted_class) {
        probMap.set(alt.class, p);
        used += p;
      }
    }

    const remainingTaxa = TAXA_REFERENCE.filter(
      (t) => t.name !== pred.predicted_class && !(pred.alternatives ?? []).some((a) => a.class === t.name)
    );
    const leftover = Math.max(0, 100 - used);
    const each = remainingTaxa.length > 0 ? leftover / remainingTaxa.length : 0;
    for (const t of remainingTaxa) {
      probMap.set(t.name, Number(each.toFixed(2)));
    }
  }

  return Array.from(probMap.entries())
    .map(([taxon, probability]) => ({
      taxon,
      probability: Number(probability.toFixed(2)),
      isTop: taxon.toLowerCase() === pred.predicted_class.toLowerCase(),
    }))
    .sort((a, b) => b.probability - a.probability);
}

export const api = {
  // Auth
  async login(username: string, password: string): Promise<TokenResponse> {
    const body = new URLSearchParams();
    body.set('username', username);
    body.set('password', password);
    const res = await httpClient.post<TokenResponse>('/auth/token', body, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    return res.data;
  },

  async register(payload: RegisterRequest): Promise<User> {
    const res = await httpClient.post<User>('/auth/register', payload);
    return res.data;
  },

  async getMe(): Promise<User> {
    const res = await httpClient.get<User>('/auth/me');
    return res.data;
  },

  async updateMe(fullName: string): Promise<User> {
    const res = await httpClient.patch<User>('/auth/me', { full_name: fullName });
    return res.data;
  },

  async getAuthProviders(): Promise<AuthProvidersResponse> {
    const res = await httpClient.get<AuthProvidersResponse>('/auth/providers', { timeout: 8000 });
    return res.data;
  },

  /** Full-page navigation target that starts Google/GitHub sign-in on the backend */
  oauthLoginUrl(provider: OAuthProvider): string {
    return `${useAuthStore.getState().getEffectiveApiBaseUrl()}/auth/oauth/${provider}/login`;
  },

  async logout(): Promise<void> {
    try {
      const refreshToken = useAuthStore.getState().refreshToken;
      await httpClient.post('/auth/logout', refreshToken ? { refresh_token: refreshToken } : undefined);
    } catch {
      // Clear local session regardless of server logout response
    }
  },

  async updateUser(userId: number, update: UserUpdateRequest): Promise<User> {
    const res = await httpClient.patch<User>(`/auth/users/${userId}`, update);
    return res.data;
  },

  // My workspace
  async getMyStats(): Promise<MyStatsResponse> {
    const res = await httpClient.get<MyStatsResponse>('/me/stats');
    return res.data;
  },

  async getMyPredictions(limit = 25, offset = 0, taxon?: string): Promise<PaginatedResponse<PredictionHistoryItem>> {
    const res = await httpClient.get<PaginatedResponse<PredictionHistoryItem>>('/me/predictions', {
      params: { limit, offset, ...(taxon ? { taxon } : {}) },
    });
    return res.data;
  },

  async getMyJobs(limit = 25, offset = 0): Promise<PaginatedResponse<JobHistoryItem>> {
    const res = await httpClient.get<PaginatedResponse<JobHistoryItem>>('/me/jobs', {
      params: { limit, offset },
    });
    return res.data;
  },

  async listUsers(): Promise<UsersListResponse> {
    const res = await httpClient.get<UsersListResponse>('/auth/users');
    return {
      total: res.data?.total ?? res.data?.users?.length ?? 0,
      users: Array.isArray(res.data?.users) ? res.data.users : [],
    };
  },

  async getPermissions(): Promise<PermissionsResponse> {
    const res = await httpClient.get<PermissionsResponse>('/auth/permissions');
    return {
      roles: Array.isArray(res.data?.roles) ? res.data.roles : [],
      all_permissions: Array.isArray(res.data?.all_permissions) ? res.data.all_permissions : [],
    };
  },

  async getSecurityStats(): Promise<SecurityStatsResponse> {
    const res = await httpClient.get<SecurityStatsResponse>('/auth/security-stats');
    return res.data ?? {
      total_users: 0,
      active_users: 0,
      disabled_users: 0,
      users_by_role: {},
      revoked_tokens: 0,
    };
  },

  // Health & Monitoring
  async getHealth(): Promise<HealthResponse> {
    const res = await httpClient.get<HealthResponse>('/health', { timeout: 8000 });
    return res.data ?? { status: 'unknown' };
  },

  async getDetailedHealth(): Promise<DetailedHealthResponse> {
    const res = await httpClient.get<DetailedHealthResponse>('/health/detailed');
    return res.data ?? { status: 'unknown' };
  },

  async getSystemMetrics(): Promise<SystemHealthResponse> {
    const res = await httpClient.get<SystemHealthResponse>('/monitoring/system');
    return res.data ?? {};
  },

  async getApplicationMetrics(): Promise<ApplicationMetricsResponse> {
    const res = await httpClient.get<ApplicationMetricsResponse>('/monitoring/application');
    return res.data ?? {};
  },

  async getDashboardMetrics(): Promise<DashboardMetricsResponse> {
    const res = await httpClient.get<DashboardMetricsResponse>('/monitoring/dashboard');
    return res.data ?? {};
  },

  // Cache
  async getCacheStats(): Promise<CacheStatsResponse> {
    const res = await httpClient.get<CacheStatsResponse>('/cache/stats');
    return res.data ?? {};
  },

  async getCacheBackend(): Promise<{ backend?: string; description?: string }> {
    const res = await httpClient.get<{ backend?: string; description?: string }>('/cache/backend');
    return res.data ?? {};
  },

  async clearCache(pattern?: string): Promise<CacheClearResponse> {
    const res = await httpClient.post<CacheClearResponse>('/cache/clear', null, {
      params: pattern ? { pattern } : undefined,
    });
    return res.data;
  },

  // Prediction & Classification
  async predictSingle(sequence: string, useCache = true): Promise<PredictResponse> {
    const res = await httpClient.post<Record<string, unknown>>('/api/predict', {
      sequence,
      use_cache: useCache,
    });
    return normalizePredictResponse(res.data ?? {});
  },

  async predictBatch(
    sequences: Array<{ id: string; sequence: string }>,
    useCache = true
  ): Promise<BatchPredictResponse> {
    const res = await httpClient.post<Record<string, unknown>>('/api/predict/batch', {
      sequences: sequences.map((s) => s.sequence),
      sequence_ids: sequences.map((s) => s.id),
      use_cache: useCache,
    });
    const data = res.data ?? {};
    const rawList = Array.isArray(data.predictions)
      ? data.predictions
      : Array.isArray(data.results)
      ? data.results
      : Array.isArray(data)
      ? data
      : [];

    const normalized = rawList.map((item, idx) => {
      const obj = (item ?? {}) as Record<string, unknown>;
      const pred = normalizePredictResponse(obj);
      return {
        sequence_id:
          typeof obj.sequence_id === 'string'
            ? obj.sequence_id
            : typeof obj.id === 'string'
            ? obj.id
            : sequences[idx]?.id ?? `seq_${idx + 1}`,
        sequence: sequences[idx]?.sequence,
        predicted_class: pred.predicted_class,
        confidence: pred.confidence,
        alternatives: pred.alternatives,
        is_novel: pred.is_novel,
        cached: pred.cached,
      };
    });

    return {
      total: typeof data.total === 'number' ? data.total : normalized.length,
      predictions: normalized,
      summary: data.summary as BatchPredictResponse['summary'],
    };
  },

  async uploadFileAndStartAnalysis(
    files: File[],
    onProgress?: (percent: number) => void
  ): Promise<{ jobId: string; uploadInfo: UploadResponse }> {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
    }

    const uploadRes = await httpClient.post<UploadResponse>('/api/upload', formData, {
      timeout: 300000,
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (evt) => {
        if (onProgress && evt.total) {
          onProgress(Math.round((evt.loaded * 100) / evt.total));
        }
      },
    });

    const uploadData = uploadRes.data ?? {};
    const directJobId = uploadData.jobId ?? uploadData.job_id;
    if (directJobId) {
      return { jobId: directJobId, uploadInfo: uploadData };
    }

    const filePaths = uploadData.file_paths ?? [];
    if (filePaths.length === 0) {
      throw new Error('Upload succeeded but returned no jobId or file_paths to analyze.');
    }

    const analyzeRes = await httpClient.post<AnalysisJobResponse>('/api/analyze', {
      file_paths: filePaths,
    });

    return {
      jobId: analyzeRes.data.jobId,
      uploadInfo: uploadData,
    };
  },

  async getAnalysisResults(jobId: string): Promise<AnalysisJobResponse> {
    const res = await httpClient.get<AnalysisJobResponse>(`/api/results/${encodeURIComponent(jobId)}`);
    return res.data;
  },

  async exportResults(jobId: string, format: ExportFormat): Promise<{ blob: Blob; filename: string }> {
    const res = await httpClient.get<Blob>(`/api/export/${encodeURIComponent(jobId)}`, {
      params: { format },
      responseType: 'blob',
    });
    const disposition = res.headers['content-disposition'] as string | undefined;
    const match = disposition?.match(/filename="?([^"]+)"?/i);
    const filename = match?.[1] || `deepsea_edna_${jobId}.${format}`;
    return { blob: res.data, filename };
  },

  // Model serving (MLflow registry champion or bundled files)
  async getServingModel(): Promise<ServingModelInfo> {
    const res = await httpClient.get<ServingModelInfo>('/api/model/serving');
    return res.data;
  },

  async reloadServingModel(): Promise<ServingModelInfo> {
    const res = await httpClient.post<ServingModelInfo>('/api/model/reload', null, { timeout: 120000 });
    return res.data;
  },

  // Model Registry & Versions
  async listVersions(status?: string, limit = 50): Promise<VersionListResponse> {
    const res = await httpClient.get<VersionListResponse>('/versions', {
      params: { ...(status ? { status } : {}), limit },
    });
    return {
      total: res.data?.total ?? res.data?.versions?.length ?? 0,
      versions: Array.isArray(res.data?.versions) ? res.data.versions : [],
    };
  },

  async getCurrentVersionInfo(): Promise<Partial<ModelVersion>> {
    const res = await httpClient.get<Partial<ModelVersion>>('/versions/current/info');
    return res.data ?? {};
  },

  async getVersionDetails(versionId: string): Promise<ModelVersion> {
    const res = await httpClient.get<ModelVersion>(`/versions/${encodeURIComponent(versionId)}`);
    return res.data;
  },

  async compareVersions(id1: string, id2: string): Promise<VersionComparisonResponse> {
    const res = await httpClient.get<VersionComparisonResponse>(
      `/versions/compare/${encodeURIComponent(id1)}/${encodeURIComponent(id2)}`
    );
    return res.data;
  },

  async getVersionHistory(limit = 20): Promise<VersionHistoryResponse> {
    const res = await httpClient.get<VersionHistoryResponse>('/versions/history', {
      params: { limit },
    });
    return {
      total: res.data?.total ?? res.data?.history?.length ?? 0,
      history: Array.isArray(res.data?.history) ? res.data.history : [],
    };
  },

  async registerVersion(payload: {
    version_name: string;
    model_path: string;
    description?: string;
    metrics?: Record<string, number>;
    tags?: string[];
    set_as_current?: boolean;
  }): Promise<{ version_id: string; version_name: string; is_current: boolean }> {
    const res = await httpClient.post('/versions/register', {
      metrics: payload.metrics,
      tags: payload.tags,
    }, {
      params: {
        version_name: payload.version_name,
        model_path: payload.model_path,
        description: payload.description ?? '',
        set_as_current: payload.set_as_current ?? false,
      },
    });
    return res.data;
  },

  async activateVersion(versionId: string): Promise<void> {
    await httpClient.post(`/versions/${encodeURIComponent(versionId)}/activate`);
  },

  async rollbackVersion(versionId: string): Promise<void> {
    await httpClient.post(`/versions/${encodeURIComponent(versionId)}/rollback`);
  },

  async archiveVersion(versionId: string): Promise<void> {
    await httpClient.post(`/versions/${encodeURIComponent(versionId)}/archive`);
  },

  async deprecateVersion(versionId: string, reason = ''): Promise<void> {
    await httpClient.post(`/versions/${encodeURIComponent(versionId)}/deprecate`, null, {
      params: reason ? { reason } : undefined,
    });
  },

  // A/B Testing Experiments
  async listExperiments(status?: string): Promise<ExperimentsListResponse> {
    const res = await httpClient.get<ExperimentsListResponse>('/experiments', {
      params: status ? { status } : undefined,
    });
    return {
      total: res.data?.total ?? res.data?.experiments?.length ?? 0,
      experiments: Array.isArray(res.data?.experiments) ? res.data.experiments : [],
    };
  },

  async getActiveExperiment(): Promise<{ active: boolean; experiment: Experiment | null }> {
    const res = await httpClient.get<{ active: boolean; experiment: Experiment | null }>('/experiments/active');
    return res.data ?? { active: false, experiment: null };
  },

  async createExperiment(payload: {
    experiment_name: string;
    description: string;
    variant_configs: Array<{ name: string; version_id: string; traffic: number }>;
    strategy?: string;
    duration_days?: number;
  }): Promise<{ experiment_id: string; experiment_name: string }> {
    const res = await httpClient.post(
      '/experiments/create',
      payload.variant_configs,
      {
        params: {
          experiment_name: payload.experiment_name,
          description: payload.description,
          strategy: payload.strategy ?? 'random',
          ...(payload.duration_days ? { duration_days: payload.duration_days } : {}),
        },
      }
    );
    return res.data;
  },

  async startExperiment(id: string): Promise<void> {
    await httpClient.post(`/experiments/${encodeURIComponent(id)}/start`);
  },

  async pauseExperiment(id: string): Promise<void> {
    await httpClient.post(`/experiments/${encodeURIComponent(id)}/pause`);
  },

  async resumeExperiment(id: string): Promise<void> {
    await httpClient.post(`/experiments/${encodeURIComponent(id)}/resume`);
  },

  async stopExperiment(id: string, declareWinner = true): Promise<void> {
    await httpClient.post(`/experiments/${encodeURIComponent(id)}/stop`, null, {
      params: { declare_winner: declareWinner },
    });
  },

  async getExperimentResults(id: string): Promise<ExperimentResultsResponse> {
    const res = await httpClient.get<ExperimentResultsResponse>(
      `/experiments/${encodeURIComponent(id)}/results`
    );
    return res.data;
  },
};
