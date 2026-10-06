import { AppProviders } from "@/app/providers";
import { AppRouter } from "@/app/router";
import { AuthProvider } from "@/app/auth";

export function App() {
  return (
    <AppProviders>
      <AuthProvider>
        <AppRouter />
      </AuthProvider>
    </AppProviders>
  );
}
