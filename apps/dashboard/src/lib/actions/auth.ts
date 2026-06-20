"use server";

import { AuthError } from "next-auth";
import { signIn, signOut } from "@/auth";
import { loginSchema } from "@/lib/schemas";

export type LoginState = { error?: string };

/** Login Server Action — credentials + TOTP, then redirect to the overview. */
export async function loginAction(
  _prev: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const parsed = loginSchema.safeParse({
    username: formData.get("username"),
    password: formData.get("password"),
    token: formData.get("token"),
  });
  if (!parsed.success) {
    return { error: "Enter your username, password and the 6-digit code." };
  }

  try {
    await signIn("credentials", { ...parsed.data, redirectTo: "/" });
  } catch (err) {
    // A successful signIn throws a redirect; let that propagate.
    if (err instanceof AuthError) {
      return { error: "Invalid credentials or 2FA code." };
    }
    throw err;
  }
  return {};
}

export async function logoutAction() {
  await signOut({ redirectTo: "/login" });
}
