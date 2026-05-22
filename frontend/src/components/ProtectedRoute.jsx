import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

export const ProtectedRoute = ({ children, requireSub = true, adminOnly = false }) => {
  const { user, subscription, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="min-h-screen flex items-center justify-center text-zinc-500">Lade…</div>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />;
  if (adminOnly && user.role !== "admin") return <Navigate to="/app/vergleich" replace />;
  if (requireSub && user.role !== "admin" && !subscription?.active) {
    return <Navigate to="/abo" replace />;
  }
  return children;
};
