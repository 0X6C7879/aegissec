import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { API_AUTH_EXPIRED_EVENT, getAuthStatus, hasApiBasicCredentials } from "../lib/api";

type GateState = "loading" | "open" | "authorized" | "unauthorized";

export function AuthGate() {
  const location = useLocation();
  const [gateState, setGateState] = useState<GateState>("loading");

  useEffect(() => {
    let disposed = false;

    async function resolveGateState(): Promise<void> {
      try {
        const authStatus = await getAuthStatus();
        if (disposed) {
          return;
        }

        if (authStatus.mode !== "basic") {
          setGateState("open");
          return;
        }

        setGateState(hasApiBasicCredentials() ? "authorized" : "unauthorized");
      } catch {
        if (!disposed) {
          setGateState("open");
        }
      }
    }

    const handleAuthExpired = () => {
      if (!disposed) {
        setGateState("unauthorized");
      }
    };

    if (typeof window !== "undefined") {
      window.addEventListener(API_AUTH_EXPIRED_EVENT, handleAuthExpired);
    }

    void resolveGateState();

    return () => {
      disposed = true;
      if (typeof window !== "undefined") {
        window.removeEventListener(API_AUTH_EXPIRED_EVENT, handleAuthExpired);
      }
    };
  }, []);

  if (gateState === "loading") {
    return (
      <section className="auth-login-shell">
        <div className="auth-login-card">
          <h1 className="auth-login-title">正在检查登录状态</h1>
          <p className="auth-login-copy">请稍候，正在连接后端服务。</p>
        </div>
      </section>
    );
  }

  if (gateState === "unauthorized") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
