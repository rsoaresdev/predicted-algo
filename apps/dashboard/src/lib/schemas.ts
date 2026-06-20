import { z } from "zod";

/** Login credentials: admin username + password + 6-digit TOTP code. */
export const loginSchema = z.object({
  username: z.string().min(1).max(64),
  password: z.string().min(1).max(256),
  token: z
    .string()
    .transform((s) => s.replace(/\s+/g, ""))
    .pipe(z.string().regex(/^\d{6}$/, "Enter the 6-digit code")),
});

export type LoginInput = z.infer<typeof loginSchema>;
