import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { homeFor, useAuth } from "@/lib/auth";
import type { Role } from "@/lib/types";
import App from "@/App";
import { WelcomePage } from "@/pages/WelcomePage";
import { SignInPage } from "@/pages/SignInPage";
import { SignUpPage } from "@/pages/SignUpPage";
import { OrderPage } from "@/pages/OrderPage";

/**
 * Gate a route behind authentication and a specific role.
 * - Not signed in → send to /signin.
 * - Signed in with the wrong role → bounce to that user's own home.
 */
/** Full-screen placeholder shown while the session is being restored on first load. */
function AuthSplash() {
  return (
    <div className="grid min-h-svh place-items-center cockpit-bg">
      <p className="text-sm text-muted-foreground">Loading…</p>
    </div>
  );
}

function ProtectedRoute({ role, children }: { role: Role; children: ReactNode }) {
  const { user, initializing } = useAuth();

  if (initializing) {
    return <AuthSplash />;
  }
  if (!user) {
    return <Navigate to="/signin" replace />;
  }
  if (user.role !== role) {
    return <Navigate to={homeFor(user.role)} replace />;
  }
  return <>{children}</>;
}

export function AppRoutes() {
  const { user, initializing } = useAuth();

  if (initializing) {
    return <AuthSplash />;
  }

  return (
    <Routes>
      {/* Landing — a signed-in user is sent straight to their role home. */}
      <Route
        path="/"
        element={user ? <Navigate to={homeFor(user.role)} replace /> : <WelcomePage />}
      />
      <Route path="/signin" element={<SignInPage />} />
      <Route path="/signup" element={<SignUpPage />} />

      <Route
        path="/app"
        element={
          <ProtectedRoute role="admin">
            <App />
          </ProtectedRoute>
        }
      />
      <Route
        path="/order"
        element={
          <ProtectedRoute role="user">
            <OrderPage />
          </ProtectedRoute>
        }
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
