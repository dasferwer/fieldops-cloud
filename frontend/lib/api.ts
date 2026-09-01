export type UserRole = 'admin' | 'dispatcher' | 'technician';
export type WorkOrderPriority = 'low' | 'medium' | 'high' | 'critical';
export type WorkOrderStatus =
  | 'new'
  | 'assigned'
  | 'en_route'
  | 'in_progress'
  | 'blocked'
  | 'completed'
  | 'cancelled';

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
};

export type Site = {
  id: string;
  name: string;
  address: string;
  contact_name: string;
  contact_phone: string;
};

export type WorkOrder = {
  id: string;
  number: string;
  title: string;
  description: string;
  site_id: string;
  site_name: string;
  site_address: string;
  assignee_id: string | null;
  assignee_name: string | null;
  priority: WorkOrderPriority;
  status: WorkOrderStatus;
  scheduled_for: string;
  sla_due_at: string;
  sla_breached: boolean;
  version: number;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkOrderPage = {
  items: WorkOrder[];
  total: number;
  limit: number;
  offset: number;
};

export type Dashboard = {
  active_orders: number;
  completed_today: number;
  technicians_on_duty: number;
  overdue_orders: number;
  sla_percent: number;
  status_counts: Record<string, number>;
  priority_counts: Record<string, number>;
  upcoming: WorkOrder[];
};

export type WorkOrderInput = {
  title: string;
  description: string;
  site_id: string;
  assignee_id: string | null;
  priority: WorkOrderPriority;
  scheduled_for: string;
  sla_due_at: string;
};

const API_URL =
  process.env.NEXT_PUBLIC_FIELDOPS_API_URL ?? 'http://localhost:8061';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  token?: string,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init?.body && !(init.body instanceof FormData))
    headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    const detail =
      typeof payload?.detail === 'string'
        ? payload.detail
        : 'Запрос не выполнен';
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export async function login(email: string, password: string): Promise<string> {
  const body = new URLSearchParams({ username: email, password });
  const response = await fetch(`${API_URL}/api/v1/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!response.ok)
    throw new ApiError('Неверный email или пароль', response.status);
  const payload = (await response.json()) as { access_token: string };
  return payload.access_token;
}

export const fieldOpsApi = {
  me: (token: string) => request<User>('/api/v1/auth/me', token),
  dashboard: (token: string) => request<Dashboard>('/api/v1/dashboard', token),
  sites: (token: string) => request<Site[]>('/api/v1/sites', token),
  technicians: (token: string) => request<User[]>('/api/v1/technicians', token),
  workOrders: (token: string, search: string, status: string) => {
    const params = new URLSearchParams({ limit: '50' });
    if (search.trim()) params.set('search', search.trim());
    if (status) params.set('status', status);
    return request<WorkOrderPage>(`/api/v1/work-orders?${params}`, token);
  },
  createWorkOrder: (token: string, payload: WorkOrderInput) =>
    request<WorkOrder>('/api/v1/work-orders', token, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(payload),
    }),
  transition: (
    token: string,
    order: WorkOrder,
    status: WorkOrderStatus,
    comment?: string,
  ) =>
    request<WorkOrder>(`/api/v1/work-orders/${order.id}/transition`, token, {
      method: 'POST',
      body: JSON.stringify({
        status,
        version: order.version,
        comment: comment || null,
      }),
    }),
};

export function realtimeUrl(token: string): string {
  const url = new URL(API_URL);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = '/api/v1/realtime';
  url.searchParams.set('token', token);
  return url.toString();
}
