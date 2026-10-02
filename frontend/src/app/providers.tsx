import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRef, type ReactNode } from "react";
import { ApiError } from "@/services/api/client";
import { ErrorBoundary } from "@/app/ErrorBoundary";
import { NotificationProvider, useNotifications } from "@/app/notifications";

function notifyError(error: unknown, notify: ReturnType<typeof useNotifications>["notify"]): void {
  if (error instanceof ApiError) {
    notify({ kind: "error", message: error.message, requestId: error.requestId });
    return;
  }

  notify({ kind: "error", message: "操作未完成，请稍后重试。" });
}

function QueryProvider({ children }: { children: ReactNode }) {
  const { notify } = useNotifications();
  const clientRef = useRef<QueryClient | null>(null);

  if (!clientRef.current) {
    clientRef.current = new QueryClient({
      queryCache: new QueryCache({ onError: (error) => notifyError(error, notify) }),
      mutationCache: new MutationCache({ onError: (error) => notifyError(error, notify) }),
      defaultOptions: {
        queries: {
          retry: 1,
          staleTime: 15_000,
          refetchOnWindowFocus: false,
        },
        mutations: {
          retry: 0,
        },
      },
    });
  }

  return <QueryClientProvider client={clientRef.current}>{children}</QueryClientProvider>;
}

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary>
      <NotificationProvider>
        <QueryProvider>{children}</QueryProvider>
      </NotificationProvider>
    </ErrorBoundary>
  );
}
