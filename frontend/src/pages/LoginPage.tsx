import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/auth";
import { errorMessage } from "@/services/api";

const schema = z.object({
  email: z.string().trim().email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});
type Form = z.infer<typeof schema>;

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const loc = useLocation();
  const [serverError, setServerError] = useState<string | null>(null);
  const form = useForm<Form>({ resolver: zodResolver(schema), mode: "onTouched" });
  const { errors, isSubmitting, isValid } = form.formState;

  if (user) return <Navigate to={(loc.state as { from?: string })?.from ?? "/"} replace />;

  const onSubmit = form.handleSubmit(async (v) => {
    setServerError(null);
    try {
      await login(v.email, v.password);
      navigate((loc.state as { from?: string })?.from ?? "/", { replace: true });
    } catch (e) {
      setServerError(errorMessage(e));
    }
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm overflow-hidden">
        {/* The full logo has dark lettering, so it always sits on the brand cream (legible in dark mode too). */}
        <div className="border-b bg-brand-cream px-8 pb-5 pt-7">
          <img src="/logo-full.png" alt="TestMyLLM: Evaluate · Test · Analyze · Improve" className="mx-auto w-full max-w-[320px]" draggable={false} />
        </div>
        <CardHeader className="items-center pt-5 text-center">
          <CardTitle className="text-lg">Sign in to TestMyLLM</CardTitle>
          <CardDescription>Test LLM responses, RAG pipelines and regressions.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <Field label="Email" htmlFor="email" error={errors.email?.message}>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                autoFocus
                aria-invalid={!!errors.email}
                {...form.register("email")}
              />
            </Field>
            <Field label="Password" htmlFor="password" error={errors.password?.message}>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                aria-invalid={!!errors.password}
                {...form.register("password")}
              />
            </Field>
            {serverError && (
              <p role="alert" className="rounded-md bg-fail-bg px-3 py-2 text-sm text-fail">
                {serverError}
              </p>
            )}
            <Button type="submit" className="w-full" loading={isSubmitting} disabled={!isValid}>
              Sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
