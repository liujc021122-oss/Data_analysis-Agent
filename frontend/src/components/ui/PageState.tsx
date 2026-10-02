import { AlertCircle, CheckCircle2, Inbox, LoaderCircle, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/Button";

export type PageStateKind = "loading" | "empty" | "error";

interface PageStateAction {
  label: string;
  onClick: () => void;
}

export interface PageStateProps {
  kind: PageStateKind;
  title: string;
  description?: string;
  action?: PageStateAction;
}

const stateIcons: Record<PageStateKind, LucideIcon> = {
  loading: LoaderCircle,
  empty: Inbox,
  error: AlertCircle,
};

export function PageState({ kind, title, description, action }: PageStateProps) {
  const Icon = stateIcons[kind];
  const role = kind === "error" ? "alert" : "status";

  return (
    <section className={`page-state page-state-${kind}`} role={role} aria-live={kind === "error" ? "assertive" : "polite"}>
      <span className="page-state-icon" aria-hidden="true">
        <Icon className={kind === "loading" ? "state-spinner" : undefined} size={24} />
      </span>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {action ? <Button variant={kind === "error" ? "secondary" : "primary"} onClick={action.onClick}>{action.label}</Button> : null}
    </section>
  );
}

export function SuccessState({ title, description }: Omit<PageStateProps, "kind" | "action">) {
  return (
    <section className="page-state page-state-success" role="status">
      <span className="page-state-icon" aria-hidden="true"><CheckCircle2 size={24} /></span>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
    </section>
  );
}
