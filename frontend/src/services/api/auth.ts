import { apiClient } from "@/services/api/client";
import type { AuthUser, LoginCredentials } from "@/types/auth";

export function getCurrentUser(): Promise<AuthUser> {
  return apiClient.get<AuthUser>("/auth/me");
}

export function login(credentials: LoginCredentials): Promise<AuthUser> {
  return apiClient.post<AuthUser>("/auth/login", credentials);
}

export function logout(): Promise<void> {
  return apiClient.post<void>("/auth/logout");
}
