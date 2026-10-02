import { CheckCircle2, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

export function ToastIcon({ kind }: { kind: "success" | "error" | "info" }): ReactNode {
  if (kind === "success") return <CheckCircle2 size={17} aria-hidden="true" />;
  if (kind === "error") return <TriangleAlert size={17} aria-hidden="true" />;
  return <Info size={17} aria-hidden="true" />;
}
