import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import {
  clearApiBasicCredentials,
  getAuthStatus,
  hasApiBasicCredentials,
  isApiError,
  loginWithCredentials,
  setApiBasicCredentials,
} from "../lib/api";

type AuthModeState = "loading" | "basic" | "open";

type LocationFromState = {
  from?: {
    pathname?: string;
    search?: string;
    hash?: string;
  };
};

export function AuthLoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [modeState, setModeState] = useState<AuthModeState>("loading");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const redirectTarget = useMemo(() => {
    const locationState = location.state as LocationFromState | null;
    const from = locationState?.from;
    if (!from?.pathname) {
      return "/sessions";
    }

    return `${from.pathname}${from.search ?? ""}${from.hash ?? ""}`;
  }, [location.state]);

  useEffect(() => {
    let disposed = false;

    async function resolveModeState(): Promise<void> {
      try {
        const authStatus = await getAuthStatus();
        if (!disposed) {
          setModeState(authStatus.mode === "basic" ? "basic" : "open");
        }
      } catch {
        if (!disposed) {
          setModeState("open");
        }
      }
    }

    void resolveModeState();

    return () => {
      disposed = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setErrorMessage(null);

    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      setErrorMessage("请输入用户名和密码。");
      return;
    }

    setIsSubmitting(true);
    clearApiBasicCredentials();
    try {
      await loginWithCredentials({
        username: normalizedUsername,
        password,
      });
      setApiBasicCredentials(normalizedUsername, password);
      navigate(redirectTarget, { replace: true });
    } catch (error) {
      if (isApiError(error) && error.status === 401) {
        setErrorMessage("用户名或密码错误，请重试。");
      } else {
        setErrorMessage(error instanceof Error ? error.message : "登录失败，请稍后再试。");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  if (modeState === "loading") {
    return (
      <section className="auth-login-shell">
        <div className="auth-login-card">
          <h1 className="auth-login-title">正在加载登录页</h1>
          <p className="auth-login-copy">请稍候，正在读取认证配置。</p>
        </div>
      </section>
    );
  }

  if (modeState === "open") {
    return <Navigate to="/sessions" replace />;
  }

  if (hasApiBasicCredentials()) {
    return <Navigate to={redirectTarget} replace />;
  }

  return (
    <section className="auth-login-shell">
      <div className="auth-login-card">
        <p className="auth-login-kicker">AegisSec Access</p>
        <h1 className="auth-login-title">登录工作台</h1>
        <p className="auth-login-copy">请输入启动脚本中配置的用户名和密码。</p>

        <form className="auth-login-form" onSubmit={(event) => void handleSubmit(event)}>
          <label className="auth-login-field" htmlFor="auth-login-username">
            <span>用户名</span>
            <input
              id="auth-login-username"
              className="field-input"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="请输入用户名"
              disabled={isSubmitting}
            />
          </label>

          <label className="auth-login-field" htmlFor="auth-login-password">
            <span>密码</span>
            <input
              id="auth-login-password"
              className="field-input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="请输入密码"
              disabled={isSubmitting}
            />
          </label>

          {errorMessage ? <p className="auth-login-error">{errorMessage}</p> : null}

          <button
            className="button button-primary auth-login-submit"
            type="submit"
            disabled={isSubmitting}
          >
            {isSubmitting ? "正在验证..." : "登录"}
          </button>
        </form>
      </div>
    </section>
  );
}
