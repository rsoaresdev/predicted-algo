import { redirect } from "next/navigation";
import { LineChart, Layers, ShieldCheck } from "lucide-react";
import { auth } from "@/auth";
import { LoginForm } from "@/components/login-form";

export const metadata = { title: "Sign in · PredictedAlgo" };

function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`font-semibold tracking-tight ${className}`}>
      Predicted<span className="text-gradient-primary">Algo</span>
    </span>
  );
}

const FEATURES = [
  { icon: LineChart, title: "Live equity & drawdown", desc: "Mark-to-market account performance, updated daily." },
  { icon: Layers, title: "Strategy & asset breakdown", desc: "Win rate, profit factor and P&L for every book." },
  { icon: ShieldCheck, title: "Secured by design", desc: "Read-only access, password and two-factor protected." },
];

export default async function LoginPage() {
  const session = await auth();
  if (session?.user) redirect("/");

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      {/* Brand / value panel — desktop only */}
      <div className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-sidebar/30 p-10 lg:flex xl:p-14">
        <div
          className="pointer-events-none absolute -right-24 -top-24 size-136 rounded-full opacity-60 blur-3xl"
          style={{
            background:
              "radial-gradient(closest-side, color-mix(in oklch, var(--primary) 28%, transparent), transparent)",
          }}
        />
        <div className="relative">
          <Wordmark className="text-xl" />
          <div className="mt-1 text-xs uppercase tracking-[0.18em] text-muted-foreground">
            Trading performance desk
          </div>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-balance text-3xl font-semibold leading-tight tracking-tight xl:text-4xl">
            Every trade and metric, in one{" "}
            <span className="text-gradient-primary">command center</span>.
          </h2>

          {/* Decorative equity curve */}
          <div className="surface accent-top mt-8 overflow-hidden p-4">
            <div className="flex items-center justify-between text-[11px] uppercase tracking-wider text-muted-foreground">
              <span>Account equity</span>
              <span className="inline-flex items-center gap-1.5 text-gain">
                <span className="live-dot scale-75" /> Live
              </span>
            </div>
            <svg viewBox="0 0 320 90" className="mt-2 h-24 w-full" preserveAspectRatio="none">
              <defs>
                <linearGradient id="loginFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--gain)" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="var(--gain)" stopOpacity="0" />
                </linearGradient>
              </defs>
              <path
                d="M0,72 L24,68 L48,74 L72,60 L96,63 L120,48 L144,52 L168,38 L192,42 L216,28 L240,33 L264,20 L288,24 L320,10 L320,90 L0,90 Z"
                fill="url(#loginFill)"
              />
              <path
                d="M0,72 L24,68 L48,74 L72,60 L96,63 L120,48 L144,52 L168,38 L192,42 L216,28 L240,33 L264,20 L288,24 L320,10"
                fill="none"
                stroke="var(--gain)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>

          <ul className="mt-8 space-y-4">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <li key={title} className="flex items-start gap-3">
                <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-primary/20">
                  <Icon className="size-4" />
                </span>
                <div className="leading-tight">
                  <div className="text-sm font-medium">{title}</div>
                  <div className="text-xs text-muted-foreground">{desc}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="relative text-xs text-muted-foreground">
          © {new Date().getFullYear()} PredictedAlgo · Private dashboard
        </div>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Wordmark className="text-xl" />
          </div>

          <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Sign in to view your trading performance.
          </p>

          <div className="surface accent-top mt-7 p-6">
            <LoginForm />
          </div>

          <p className="mt-6 flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
            <ShieldCheck className="size-3.5" />
            Protected by password + two-factor authentication.
          </p>
        </div>
      </div>
    </div>
  );
}
