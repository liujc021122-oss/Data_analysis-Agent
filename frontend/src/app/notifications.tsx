import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { X } from "lucide-react";

type NotificationKind = "success" | "error" | "info";

interface NotificationItem {
  id: number;
  kind: NotificationKind;
  message: string;
  requestId?: string;
}

interface NotificationContextValue {
  notify: (notification: Omit<NotificationItem, "id">) => void;
  dismiss: (id: number) => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

export function useNotifications(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error("useNotifications must be used inside NotificationProvider");
  }
  return context;
}

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<NotificationItem[]>([]);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const notify = useCallback((notification: Omit<NotificationItem, "id">) => {
    const id = Date.now() + Math.random();
    setItems((current) => [...current.slice(-3), { ...notification, id }]);
    window.setTimeout(() => dismiss(id), 5000);
  }, [dismiss]);

  const value = useMemo(() => ({ notify, dismiss }), [dismiss, notify]);

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {items.map((item) => (
          <div className={`toast toast-${item.kind}`} key={item.id} role={item.kind === "error" ? "alert" : "status"}>
            <div>
              <p>{item.message}</p>
              {item.requestId ? <small>请求编号：{item.requestId}</small> : null}
            </div>
            <button type="button" className="icon-button" aria-label="关闭提示" onClick={() => dismiss(item.id)}>
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </NotificationContext.Provider>
  );
}
