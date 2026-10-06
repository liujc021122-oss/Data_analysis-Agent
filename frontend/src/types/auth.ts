export type AuthRole = "USER" | "ADMIN";

export interface AuthUser {
  user_id: string;
  email: string;
  role: AuthRole;
  is_active: boolean;
  created_at: string;
}

export interface LoginCredentials {
  email: string;
  password: string;
}
