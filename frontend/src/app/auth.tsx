import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { getCurrentUser, login as loginRequest, logout as logoutRequest } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import type { AuthUser, LoginCredentials } from "@/types/auth";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  bootstrapError: ApiError | null;
  isLoggingIn: boolean;
  isLoggingOut: boolean;
  retryBootstrap: () => void;
  login: (credentials: LoginCredentials) => Promise<AuthUser>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  return new ApiError("NETWORK_ERROR", "无法连接 API，请确认后端服务已启动。", 0);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const requestSequence = useRef(0);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [bootstrapError, setBootstrapError] = useState<ApiError | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  const bootstrap = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setStatus("loading");
    setBootstrapError(null);

    try {
      const currentUser = await getCurrentUser();
      if (sequence !== requestSequence.current) return;
      setUser(currentUser);
      setStatus("authenticated");
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      const apiError = toApiError(error);
      setUser(null);
      if (apiError.status === 401) {
        setBootstrapError(null);
        setStatus("unauthenticated");
      } else {
        setBootstrapError(apiError);
        setStatus("error");
      }
    }
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const retryBootstrap = useCallback(() => {
    void bootstrap();
  }, [bootstrap]);

  const login = useCallback(async (credentials: LoginCredentials) => {
    requestSequence.current += 1;
    setIsLoggingIn(true);
    try {
      const loggedInUser = await loginRequest(credentials);
      queryClient.clear();
      setUser(loggedInUser);
      setBootstrapError(null);
      setStatus("authenticated");
      return loggedInUser;
    } catch (error) {
      setUser(null);
      setBootstrapError(null);
      setStatus("unauthenticated");
      throw error;
    } finally {
      setIsLoggingIn(false);
    }
  }, [queryClient]);

  const logout = useCallback(async () => {
    requestSequence.current += 1;
    setIsLoggingOut(true);
    try {
      await logoutRequest();
    } finally {
      queryClient.clear();
      setUser(null);
      setBootstrapError(null);
      setStatus("unauthenticated");
      setIsLoggingOut(false);
    }
  }, [queryClient]);

  const value: AuthContextValue = {
    status,
    user,
    bootstrapError,
    isLoggingIn,
    isLoggingOut,
    retryBootstrap,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
