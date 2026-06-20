/**
 * Server-side session guard. Every Server Action calls this first as
 * defense-in-depth — even though middleware already blocks unauthenticated
 * requests, data-returning actions independently verify the session.
 */
import "server-only";
import { auth } from "@/auth";

export async function requireSession() {
  const session = await auth();
  if (!session?.user) {
    throw new Error("Unauthorized");
  }
  return session;
}
