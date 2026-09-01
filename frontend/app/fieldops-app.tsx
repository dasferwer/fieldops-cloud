'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  Clock3,
  LayoutDashboard,
  ListTodo,
  LogOut,
  MapPin,
  Plus,
  Radio,
  Search,
  UsersRound,
  Wrench,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import {
  ApiError,
  Dashboard,
  Site,
  User,
  WorkOrder,
  WorkOrderPriority,
  WorkOrderStatus,
  fieldOpsApi,
  login,
  realtimeUrl,
} from '@/lib/api';

const statusLabels: Record<WorkOrderStatus, string> = {
  new: 'Новая',
  assigned: 'Назначена',
  en_route: 'В пути',
  in_progress: 'В работе',
  blocked: 'Заблокирована',
  completed: 'Завершена',
  cancelled: 'Отменена',
};

const priorityLabels: Record<WorkOrderPriority, string> = {
  low: 'Низкий',
  medium: 'Средний',
  high: 'Высокий',
  critical: 'Критический',
};

const nextStatus: Partial<Record<WorkOrderStatus, WorkOrderStatus>> = {
  assigned: 'en_route',
  en_route: 'in_progress',
  in_progress: 'completed',
  blocked: 'in_progress',
};

const nextStatusAction: Partial<Record<WorkOrderStatus, string>> = {
  assigned: 'Выехать',
  en_route: 'Начать',
  in_progress: 'Завершить',
  blocked: 'Возобновить',
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

function statusBadge(status: WorkOrderStatus, breached: boolean) {
  if (breached) return <Badge variant="destructive">SLA нарушен</Badge>;
  if (status === 'completed')
    return <Badge className="bg-emerald-600">Завершена</Badge>;
  if (status === 'in_progress') return <Badge>В работе</Badge>;
  if (status === 'cancelled') return <Badge variant="outline">Отменена</Badge>;
  return <Badge variant="secondary">{statusLabels[status]}</Badge>;
}

function LoginScreen({ onSuccess }: { onSuccess: (token: string) => void }) {
  const [email, setEmail] = useState('dispatcher@example.com');
  const [password, setPassword] = useState('ChangeMe123!');
  const mutation = useMutation({
    mutationFn: () => login(email, password),
    onSuccess,
  });

  return (
    <main className="grid min-h-screen place-items-center bg-ink p-5 text-white">
      <div className="absolute inset-0 opacity-40 [background-image:radial-gradient(circle_at_15%_15%,oklch(0.55_0.155_162/35%),transparent_32%),radial-gradient(circle_at_85%_80%,oklch(0.83_0.18_127/16%),transparent_30%)]" />
      <Card className="relative w-full max-w-md bg-white text-foreground shadow-2xl ring-0">
        <CardHeader className="pb-2">
          <div className="mb-5 grid size-12 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Wrench className="size-6" />
          </div>
          <CardTitle className="text-2xl">FieldOps Cloud</CardTitle>
          <CardDescription>
            Войдите в операционный центр управления выездными работами.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate();
            }}
          >
            <label
              htmlFor="fieldops-email"
              className="block space-y-1.5 text-sm font-medium"
            >
              Email
              <Input
                id="fieldops-email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                type="email"
              />
            </label>
            <label
              htmlFor="fieldops-password"
              className="block space-y-1.5 text-sm font-medium"
            >
              Пароль
              <Input
                id="fieldops-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                type="password"
              />
            </label>
            {mutation.error && (
              <Alert variant="destructive">
                <AlertCircle />
                <AlertTitle>Не удалось войти</AlertTitle>
                <AlertDescription>{mutation.error.message}</AlertDescription>
              </Alert>
            )}
            <Button className="h-10 w-full" disabled={mutation.isPending}>
              {mutation.isPending ? 'Подключение…' : 'Войти как диспетчер'}
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              Демо-учётные данные уже заполнены
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}

const createSchema = z
  .object({
    title: z.string().min(4, 'Минимум 4 символа'),
    description: z.string().min(10, 'Минимум 10 символов'),
    site_id: z.string().min(1, 'Выберите объект'),
    assignee_id: z.string(),
    priority: z.enum(['low', 'medium', 'high', 'critical']),
    scheduled_for: z.string().min(1, 'Укажите начало'),
    sla_due_at: z.string().min(1, 'Укажите срок SLA'),
  })
  .refine(
    (value) => new Date(value.sla_due_at) > new Date(value.scheduled_for),
    {
      message: 'SLA должен быть позже начала работ',
      path: ['sla_due_at'],
    },
  );

type CreateValues = z.infer<typeof createSchema>;

function localDateTime(hoursFromNow: number): string {
  const date = new Date(Date.now() + hoursFromNow * 60 * 60 * 1000);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function CreateOrderDialog({
  open,
  onOpenChange,
  token,
  sites,
  technicians,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  token: string;
  sites: Site[];
  technicians: User[];
}) {
  const queryClient = useQueryClient();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      title: '',
      description: '',
      site_id: '',
      assignee_id: '',
      priority: 'medium',
      scheduled_for: localDateTime(1),
      sla_due_at: localDateTime(6),
    },
  });
  const mutation = useMutation({
    mutationFn: (values: CreateValues) =>
      fieldOpsApi.createWorkOrder(token, {
        ...values,
        assignee_id: values.assignee_id || null,
        scheduled_for: new Date(values.scheduled_for).toISOString(),
        sla_due_at: new Date(values.sla_due_at).toISOString(),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['fieldops'] });
      reset();
      onOpenChange(false);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Новая заявка</DialogTitle>
          <DialogDescription>
            Создайте работу, назначьте SLA и при необходимости сразу выберите
            специалиста.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={handleSubmit((values) => mutation.mutate(values))}
        >
          <label
            htmlFor="order-title"
            className="grid gap-1.5 text-sm font-medium"
          >
            Заголовок
            <Input
              id="order-title"
              {...register('title')}
              placeholder="Например, диагностика оборудования"
            />
            {errors.title && (
              <span className="text-xs text-destructive">
                {errors.title.message}
              </span>
            )}
          </label>
          <label
            htmlFor="order-description"
            className="grid gap-1.5 text-sm font-medium"
          >
            Описание
            <Textarea
              id="order-description"
              {...register('description')}
              placeholder="Опишите симптомы и ожидаемый результат"
            />
            {errors.description && (
              <span className="text-xs text-destructive">
                {errors.description.message}
              </span>
            )}
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label
              htmlFor="order-site"
              className="grid gap-1.5 text-sm font-medium"
            >
              Объект
              <NativeSelect
                id="order-site"
                className="w-full"
                {...register('site_id')}
              >
                <NativeSelectOption value="">
                  Выберите объект
                </NativeSelectOption>
                {sites.map((site) => (
                  <NativeSelectOption key={site.id} value={site.id}>
                    {site.name}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              {errors.site_id && (
                <span className="text-xs text-destructive">
                  {errors.site_id.message}
                </span>
              )}
            </label>
            <label
              htmlFor="order-assignee"
              className="grid gap-1.5 text-sm font-medium"
            >
              Исполнитель
              <NativeSelect
                id="order-assignee"
                className="w-full"
                {...register('assignee_id')}
              >
                <NativeSelectOption value="">
                  Назначить позже
                </NativeSelectOption>
                {technicians.map((technician) => (
                  <NativeSelectOption key={technician.id} value={technician.id}>
                    {technician.full_name}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <label
              htmlFor="order-priority"
              className="grid gap-1.5 text-sm font-medium"
            >
              Приоритет
              <NativeSelect
                id="order-priority"
                className="w-full"
                {...register('priority')}
              >
                {Object.entries(priorityLabels).map(([value, label]) => (
                  <NativeSelectOption key={value} value={value}>
                    {label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </label>
            <label
              htmlFor="order-scheduled"
              className="grid gap-1.5 text-sm font-medium"
            >
              Начало
              <Input
                id="order-scheduled"
                type="datetime-local"
                {...register('scheduled_for')}
              />
            </label>
            <label
              htmlFor="order-sla"
              className="grid gap-1.5 text-sm font-medium"
            >
              Срок SLA
              <Input
                id="order-sla"
                type="datetime-local"
                {...register('sla_due_at')}
              />
              {errors.sla_due_at && (
                <span className="text-xs text-destructive">
                  {errors.sla_due_at.message}
                </span>
              )}
            </label>
          </div>
          {mutation.error && (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertTitle>Заявка не создана</AlertTitle>
              <AlertDescription>{mutation.error.message}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Отмена
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Сохранение…' : 'Создать заявку'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DashboardView({ dashboard }: { dashboard?: Dashboard }) {
  if (!dashboard) {
    return <Skeleton className="h-[520px] w-full rounded-xl" />;
  }
  const metrics = [
    {
      label: 'Активные заявки',
      value: dashboard.active_orders,
      note: 'в текущей очереди',
      icon: Wrench,
    },
    {
      label: 'Специалисты на линии',
      value: dashboard.technicians_on_duty,
      note: 'доступны сегодня',
      icon: UsersRound,
    },
    {
      label: 'Выполнено в SLA',
      value: `${dashboard.sla_percent}%`,
      note: 'по завершённым работам',
      icon: CheckCircle2,
    },
    {
      label: 'Требуют внимания',
      value: dashboard.overdue_orders,
      note: 'с нарушенным SLA',
      icon: AlertTriangle,
    },
  ];

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <Card
            key={metric.label}
            className="bg-card/90 shadow-[0_14px_40px_rgb(15_23_42/4%)]"
          >
            <CardHeader>
              <CardDescription>{metric.label}</CardDescription>
              <CardAction className="grid size-8 place-items-center rounded-lg bg-secondary text-primary">
                <metric.icon className="size-4" />
              </CardAction>
              <CardTitle className="text-3xl font-semibold tracking-tight">
                {metric.value}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">
              {metric.note}
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.55fr)_minmax(330px,0.75fr)]">
        <Card className="bg-card/95">
          <CardHeader className="border-b">
            <CardTitle>Ближайшие работы</CardTitle>
            <CardDescription>Очередь выездов по времени начала</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 px-2">
            {dashboard.upcoming.map((order) => (
              <article
                key={order.id}
                className="grid gap-3 rounded-xl px-3 py-4 transition-colors hover:bg-muted/60 md:grid-cols-[92px_minmax(0,1fr)_155px_auto] md:items-center"
              >
                <div>
                  <p className="font-mono text-xs font-semibold text-primary">
                    {order.number}
                  </p>
                  <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock3 className="size-3" />{' '}
                    {formatTime(order.scheduled_for)}
                  </p>
                </div>
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold">
                    {order.title}
                  </h2>
                  <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                    <MapPin className="size-3" /> {order.site_name}
                  </p>
                </div>
                <div className="text-sm">
                  <p className="truncate font-medium">
                    {order.assignee_name ?? 'Не назначен'}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {priorityLabels[order.priority]}
                  </p>
                </div>
                {statusBadge(order.status, order.sla_breached)}
              </article>
            ))}
          </CardContent>
        </Card>

        <Card className="bg-ink text-white ring-0">
          <CardHeader>
            <CardDescription className="text-white/55">
              Операционная устойчивость
            </CardDescription>
            <CardTitle className="text-xl text-white">SLA смены</CardTitle>
            <CardAction className="grid size-9 place-items-center rounded-lg bg-white/8">
              <CalendarDays className="size-4 text-lime" />
            </CardAction>
          </CardHeader>
          <CardContent>
            <div className="flex items-end justify-between">
              <div>
                <p className="text-5xl font-semibold tracking-tight">
                  {dashboard.sla_percent}%
                </p>
                <p className="mt-1 text-sm text-white/55">
                  Целевой показатель — 92%
                </p>
              </div>
              <span className="rounded-full bg-lime/12 px-2.5 py-1 text-xs font-semibold text-lime">
                {dashboard.sla_percent >= 92 ? 'В норме' : 'Риск'}
              </span>
            </div>
            <div className="mt-8 h-2 overflow-hidden rounded-full bg-white/8">
              <div
                className="h-full rounded-full bg-lime transition-all"
                style={{ width: `${Math.min(dashboard.sla_percent, 100)}%` }}
              />
            </div>
            <dl className="mt-8 grid grid-cols-2 gap-4 border-t border-white/10 pt-6">
              <div>
                <dt className="text-xs text-white/45">Завершено сегодня</dt>
                <dd className="mt-1 text-2xl font-semibold">
                  {dashboard.completed_today}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-white/45">Просрочено</dt>
                <dd className="mt-1 text-2xl font-semibold">
                  {dashboard.overdue_orders}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function OrdersView({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const orders = useQuery({
    queryKey: ['fieldops', 'orders', search, status],
    queryFn: () => fieldOpsApi.workOrders(token, search, status),
  });
  const transition = useMutation({
    mutationFn: ({
      order,
      target,
    }: {
      order: WorkOrder;
      target: WorkOrderStatus;
    }) => fieldOpsApi.transition(token, order, target),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fieldops'] }),
  });

  return (
    <Card className="bg-card/95">
      <CardHeader className="border-b">
        <CardTitle>Все заявки</CardTitle>
        <CardDescription>
          {orders.data?.total ?? '—'} записей с учётом фильтров
        </CardDescription>
        <CardAction className="flex gap-2">
          <div className="relative hidden w-56 sm:block">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Номер или название"
              className="pl-8"
            />
          </div>
          <NativeSelect
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <NativeSelectOption value="">Все статусы</NativeSelectOption>
            {Object.entries(statusLabels).map(([value, label]) => (
              <NativeSelectOption key={value} value={value}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </CardAction>
      </CardHeader>
      <CardContent className="px-2">
        {orders.isLoading && <Skeleton className="m-4 h-80" />}
        {orders.error && (
          <Alert variant="destructive" className="m-4">
            <AlertCircle />
            <AlertTitle>Не удалось загрузить заявки</AlertTitle>
            <AlertDescription>{orders.error.message}</AlertDescription>
          </Alert>
        )}
        {orders.data && orders.data.items.length === 0 && (
          <div className="grid place-items-center py-20 text-center">
            <ListTodo className="size-9 text-muted-foreground" />
            <p className="mt-3 font-medium">Заявок по фильтру нет</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Измените параметры поиска.
            </p>
          </div>
        )}
        {orders.data && orders.data.items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Заявка</TableHead>
                <TableHead>Объект</TableHead>
                <TableHead>Исполнитель</TableHead>
                <TableHead>Начало / SLA</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead className="text-right">Действие</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orders.data.items.map((order) => {
                const target = nextStatus[order.status];
                return (
                  <TableRow key={order.id}>
                    <TableCell className="max-w-64">
                      <p className="font-mono text-xs font-semibold text-primary">
                        {order.number}
                      </p>
                      <p className="mt-1 truncate font-medium">{order.title}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {priorityLabels[order.priority]} приоритет
                      </p>
                    </TableCell>
                    <TableCell>
                      <p className="font-medium">{order.site_name}</p>
                      <p className="mt-1 max-w-48 truncate text-xs text-muted-foreground">
                        {order.site_address}
                      </p>
                    </TableCell>
                    <TableCell>
                      {order.assignee_name ?? 'Не назначен'}
                    </TableCell>
                    <TableCell>
                      <p>{formatDate(order.scheduled_for)}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        SLA {formatDate(order.sla_due_at)}
                      </p>
                    </TableCell>
                    <TableCell>
                      {statusBadge(order.status, order.sla_breached)}
                    </TableCell>
                    <TableCell className="text-right">
                      {target ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={transition.isPending}
                          onClick={() => transition.mutate({ order, target })}
                        >
                          {nextStatusAction[order.status]}
                          <ArrowRight data-icon="inline-end" />
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export function FieldOpsApp() {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [view, setView] = useState<'dashboard' | 'orders'>('dashboard');
  const [createOpen, setCreateOpen] = useState(false);
  const [socketState, setSocketState] = useState<
    'connecting' | 'live' | 'offline'
  >('offline');

  useEffect(() => {
    const storedToken = sessionStorage.getItem('fieldops-token');
    queueMicrotask(() => setToken(storedToken));
  }, []);
  const enabled = Boolean(token);
  const me = useQuery({
    queryKey: ['fieldops', 'me'],
    queryFn: () => fieldOpsApi.me(token as string),
    enabled,
    retry: false,
  });
  const dashboard = useQuery({
    queryKey: ['fieldops', 'dashboard'],
    queryFn: () => fieldOpsApi.dashboard(token as string),
    enabled,
  });
  const sites = useQuery({
    queryKey: ['fieldops', 'sites'],
    queryFn: () => fieldOpsApi.sites(token as string),
    enabled,
  });
  const technicians = useQuery({
    queryKey: ['fieldops', 'technicians'],
    queryFn: () => fieldOpsApi.technicians(token as string),
    enabled,
  });

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      sessionStorage.removeItem('fieldops-token');
      queueMicrotask(() => setToken(null));
    }
  }, [me.error]);

  useEffect(() => {
    if (!token) return;
    const socket = new WebSocket(realtimeUrl(token));
    socket.onopen = () => setSocketState('live');
    socket.onmessage = () =>
      queryClient.invalidateQueries({ queryKey: ['fieldops'] });
    socket.onerror = () => setSocketState('offline');
    socket.onclose = () => setSocketState('offline');
    const heartbeat = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping');
    }, 20_000);
    return () => {
      window.clearInterval(heartbeat);
      socket.close();
    };
  }, [queryClient, token]);

  const today = useMemo(
    () =>
      new Intl.DateTimeFormat('ru-RU', {
        day: 'numeric',
        month: 'long',
      }).format(new Date()),
    [],
  );

  if (token === undefined)
    return <main className="min-h-screen bg-background" />;
  if (!token) {
    return (
      <LoginScreen
        onSuccess={(accessToken) => {
          sessionStorage.setItem('fieldops-token', accessToken);
          setToken(accessToken);
        }}
      />
    );
  }

  const logout = () => {
    sessionStorage.removeItem('fieldops-token');
    queryClient.clear();
    setToken(null);
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="grid min-h-screen lg:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="hidden border-r border-sidebar-border bg-sidebar px-5 py-6 lg:flex lg:flex-col">
          <div className="flex items-center gap-3 px-2">
            <div className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground shadow-[0_8px_24px_var(--brand-shadow)]">
              <Wrench className="size-5" />
            </div>
            <div>
              <p className="text-base font-semibold tracking-tight">FieldOps</p>
              <p className="text-xs text-muted-foreground">Service control</p>
            </div>
          </div>
          <nav aria-label="Основная навигация" className="mt-9 space-y-1">
            <Button
              variant={view === 'dashboard' ? 'secondary' : 'ghost'}
              className="w-full justify-start"
              onClick={() => setView('dashboard')}
            >
              <LayoutDashboard data-icon="inline-start" /> Обзор
            </Button>
            <Button
              variant={view === 'orders' ? 'secondary' : 'ghost'}
              className="w-full justify-start"
              onClick={() => setView('orders')}
            >
              <ListTodo data-icon="inline-start" /> Заявки
              <span className="ml-auto rounded-full bg-primary/12 px-2 py-0.5 text-xs text-primary">
                {dashboard.data?.active_orders ?? '—'}
              </span>
            </Button>
          </nav>
          <div className="mt-auto rounded-xl border border-sidebar-border bg-card/70 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              Текущий пользователь
            </p>
            <p className="mt-3 text-sm font-semibold">
              {me.data?.full_name ?? 'Загрузка…'}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {me.data?.email}
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="mt-3 px-0"
              onClick={logout}
            >
              <LogOut data-icon="inline-start" /> Выйти
            </Button>
          </div>
        </aside>

        <section className="min-w-0">
          <header className="flex min-h-18 items-center justify-between gap-4 border-b bg-card/80 px-5 py-3 backdrop-blur md:px-8">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
                Операционный центр
              </p>
              <p className="mt-0.5 text-sm font-medium">Москва · {today}</p>
            </div>
            <div className="flex items-center gap-3">
              <Badge
                variant="outline"
                className="hidden h-7 gap-1.5 bg-card px-3 sm:flex"
              >
                <Radio
                  className={`size-3 ${socketState === 'live' ? 'text-emerald-500' : 'text-amber-500'}`}
                />
                {socketState === 'live' ? 'Real-time' : 'Подключение'}
              </Badge>
              <Button size="lg" onClick={() => setCreateOpen(true)}>
                <Plus data-icon="inline-start" /> Новая заявка
              </Button>
            </div>
          </header>

          <div className="mx-auto max-w-[1500px] p-5 md:p-8">
            <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
              <div>
                <h1 className="text-3xl font-semibold tracking-[-0.035em] md:text-4xl">
                  {view === 'dashboard'
                    ? 'Работы сегодня'
                    : 'Управление заявками'}
                </h1>
                <p className="mt-2 max-w-2xl text-sm text-muted-foreground md:text-base">
                  {view === 'dashboard'
                    ? 'Контролируйте загрузку команды, соблюдение SLA и критические выезды в одном окне.'
                    : 'Фильтруйте очередь и безопасно переводите работу по жизненному циклу.'}
                </p>
              </div>
              <div className="flex gap-2 lg:hidden">
                <Button
                  variant={view === 'dashboard' ? 'secondary' : 'outline'}
                  onClick={() => setView('dashboard')}
                >
                  Обзор
                </Button>
                <Button
                  variant={view === 'orders' ? 'secondary' : 'outline'}
                  onClick={() => setView('orders')}
                >
                  Заявки
                </Button>
              </div>
            </div>

            {dashboard.error && (
              <Alert variant="destructive" className="mb-6">
                <AlertCircle />
                <AlertTitle>API недоступен</AlertTitle>
                <AlertDescription>
                  Запустите проект через Docker Compose и повторите вход.
                </AlertDescription>
              </Alert>
            )}
            {view === 'dashboard' ? (
              <DashboardView dashboard={dashboard.data} />
            ) : (
              <OrdersView token={token} />
            )}
          </div>
        </section>
      </div>

      <CreateOrderDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        token={token}
        sites={sites.data ?? []}
        technicians={technicians.data ?? []}
      />
    </main>
  );
}
