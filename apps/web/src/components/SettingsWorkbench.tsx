import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getModelApiSettings, listRuntimeProfiles, updateModelApiSettings } from "../lib/api";
import { useUiStore } from "../store/uiStore";
import type { ModelApiSettingsUpdate, ModelProvider } from "../types/settings";

const MODEL_API_SETTINGS_QUERY_KEY = ["settings", "model-api"] as const;
const RUNTIME_PROFILES_QUERY_KEY = ["runtime-profiles", "settings"] as const;

function normalizeInputValue(value: string): string | null {
  const trimmedValue = value.trim();
  return trimmedValue.length > 0 ? trimmedValue : null;
}

export function SettingsWorkbench() {
  const queryClient = useQueryClient();
  const themePreference = useUiStore((state) => state.themePreference);
  const setThemePreference = useUiStore((state) => state.setThemePreference);
  const uiDensity = useUiStore((state) => state.uiDensity);
  const setUiDensity = useUiStore((state) => state.setUiDensity);
  const [provider, setProvider] = useState<ModelProvider>("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [anthropicBaseUrl, setAnthropicBaseUrl] = useState("");
  const [anthropicModel, setAnthropicModel] = useState("");
  const [anthropicApiKey, setAnthropicApiKey] = useState("");
  const [clearAnthropicApiKey, setClearAnthropicApiKey] = useState(false);

  const settingsQuery = useQuery({
    queryKey: MODEL_API_SETTINGS_QUERY_KEY,
    queryFn: ({ signal }) => getModelApiSettings(signal),
  });

  const runtimeProfilesQuery = useQuery({
    queryKey: RUNTIME_PROFILES_QUERY_KEY,
    queryFn: ({ signal }) => listRuntimeProfiles(signal),
  });

  useEffect(() => {
    if (!settingsQuery.data) {
      return;
    }

    setProvider(settingsQuery.data.provider);
    setBaseUrl(settingsQuery.data.base_url ?? "");
    setModel(settingsQuery.data.model ?? "");
    setApiKey("");
    setClearApiKey(false);
    setAnthropicBaseUrl(settingsQuery.data.anthropic_base_url ?? "");
    setAnthropicModel(settingsQuery.data.anthropic_model ?? "");
    setAnthropicApiKey("");
    setClearAnthropicApiKey(false);
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: ModelApiSettingsUpdate) => updateModelApiSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: MODEL_API_SETTINGS_QUERY_KEY });
      setApiKey("");
      setClearApiKey(false);
      setAnthropicApiKey("");
      setClearAnthropicApiKey(false);
    },
  });

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    await saveMutation.mutateAsync({
      provider,
      base_url: normalizeInputValue(baseUrl),
      model: normalizeInputValue(model),
      api_key: clearApiKey ? null : normalizeInputValue(apiKey),
      clear_api_key: clearApiKey,
      anthropic_base_url: normalizeInputValue(anthropicBaseUrl),
      anthropic_model: normalizeInputValue(anthropicModel),
      anthropic_api_key: clearAnthropicApiKey ? null : normalizeInputValue(anthropicApiKey),
      clear_anthropic_api_key: clearAnthropicApiKey,
    });
  }

  return (
    <main className="management-workbench management-workbench-settings">
      <section className="panel settings-nav-panel">
        <div className="management-unified-header">
          <div>
            <span className="panel-kicker">Workbench Profile</span>
            <h2 className="management-section-title">Settings</h2>
          </div>
        </div>

        <section className="management-section-card management-section-card-compact">
          <div className="management-section-header">
            <h3 className="management-section-title">主题</h3>
          </div>
          <div className="segmented-control">
            <button
              className={`segmented-control-button ${themePreference === "dark" ? "segmented-control-button-active" : ""}`}
              type="button"
              onClick={() => setThemePreference("dark")}
              aria-pressed={themePreference === "dark"}
            >
              深色
            </button>
            <button
              className={`segmented-control-button ${themePreference === "light" ? "segmented-control-button-active" : ""}`}
              type="button"
              onClick={() => setThemePreference("light")}
              aria-pressed={themePreference === "light"}
            >
              浅色
            </button>
          </div>
        </section>

        <section className="management-section-card management-section-card-compact">
          <div className="management-section-header">
            <h3 className="management-section-title">界面密度</h3>
          </div>
          <div className="segmented-control">
            <button
              className={`segmented-control-button ${uiDensity === "compact" ? "segmented-control-button-active" : ""}`}
              type="button"
              onClick={() => setUiDensity("compact")}
              aria-pressed={uiDensity === "compact"}
            >
              紧凑
            </button>
            <button
              className={`segmented-control-button ${uiDensity === "comfortable" ? "segmented-control-button-active" : ""}`}
              type="button"
              onClick={() => setUiDensity("comfortable")}
              aria-pressed={uiDensity === "comfortable"}
            >
              舒展
            </button>
          </div>
        </section>

        <section className="management-section-card management-section-card-compact">
          <div className="management-section-header">
            <h3 className="management-section-title">Runtime Profiles</h3>
            <span className="management-status-badge tone-neutral">
              {runtimeProfilesQuery.data?.length ?? 0}
            </span>
          </div>
          {runtimeProfilesQuery.data?.length ? (
            <ul className="management-list settings-metric-row">
              {runtimeProfilesQuery.data.map((profile) => (
                <li key={profile.name} className="management-subcard">
                  <strong className="management-list-title">{profile.name}</strong>
                  <div className="session-graph-token-row">
                    <span className="management-token-chip">
                      网络 {profile.policy.allow_network ? "开" : "关"}
                    </span>
                    <span className="management-token-chip">
                      写入 {profile.policy.allow_write ? "开" : "关"}
                    </span>
                    <span className="management-token-chip">
                      超时 {profile.policy.max_execution_seconds}s
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : runtimeProfilesQuery.isLoading ? (
            <p className="management-empty-copy">正在读取运行策略。</p>
          ) : (
            <p className="management-empty-copy">当前没有可展示的 runtime profiles。</p>
          )}
        </section>
      </section>

      <section className="panel management-unified-panel" aria-label="模型 API 设置">
        {settingsQuery.isLoading ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">正在加载设置</p>
            <p className="management-empty-copy">模型 API 配置马上就绪。</p>
          </div>
        ) : settingsQuery.isError ? (
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">设置暂不可用</p>
            <p className="management-empty-copy">{settingsQuery.error.message}</p>
          </div>
        ) : (
          <div className="management-unified-body management-unified-stack">
            <section className="management-section-card">
              <div className="management-section-header">
                <h3 className="management-section-title">模型配置</h3>
                <span className="management-status-badge tone-neutral">Model API</span>
              </div>

              <form className="settings-form" onSubmit={(event) => void handleSubmit(event)}>
                <label className="field-label">
                  Provider
                  <select
                    className="field-input"
                    value={provider}
                    onChange={(event) => setProvider(event.target.value as ModelProvider)}
                  >
                    <option value="openai">OpenAI Compatible</option>
                    <option value="anthropic">Anthropic Claude</option>
                  </select>
                </label>

                {provider === "openai" ? (
                  <>
                    <label className="field-label">
                      Base URL
                      <input
                        className="field-input"
                        type="url"
                        value={baseUrl}
                        onChange={(event) => setBaseUrl(event.target.value)}
                        placeholder="例如：https://api.openai.com/v1"
                      />
                    </label>

                    <label className="field-label">
                      Model
                      <input
                        className="field-input"
                        type="text"
                        value={model}
                        onChange={(event) => setModel(event.target.value)}
                        placeholder="例如：gpt-5-mini"
                      />
                    </label>

                    <label className="field-label">
                      API Key
                      <input
                        className="field-input"
                        type="password"
                        autoComplete="new-password"
                        value={apiKey}
                        onChange={(event) => {
                          setApiKey(event.target.value);
                          if (clearApiKey && event.target.value.trim()) {
                            setClearApiKey(false);
                          }
                        }}
                        placeholder={
                          settingsQuery.data?.api_key_configured
                            ? "已配置，如需更新请输入新的 Key"
                            : "输入要保存的 API Key"
                        }
                      />
                    </label>

                    <label className="settings-inline-toggle">
                      <input
                        type="checkbox"
                        checked={clearApiKey}
                        onChange={(event) => {
                          setClearApiKey(event.target.checked);
                          if (event.target.checked) {
                            setApiKey("");
                          }
                        }}
                      />
                      保存时清除已保存的 API Key
                    </label>
                  </>
                ) : (
                  <>
                    <div className="management-inline-notice">
                      留空时使用默认 Anthropic 地址 <code>https://api.anthropic.com</code>
                      ；请求会自动归一化到 <code>/v1/messages</code>
                      。如果你填的是兼容网关基地址，例如 MiniMax 的{" "}
                      <code>https://api.minimaxi.com/anthropic</code>
                      ，系统也会自动补成正确消息端点。
                    </div>

                    <label className="field-label">
                      Anthropic Base URL
                      <input
                        className="field-input"
                        type="url"
                        value={anthropicBaseUrl}
                        onChange={(event) => setAnthropicBaseUrl(event.target.value)}
                        placeholder="例如：https://api.anthropic.com 或 https://api.minimaxi.com/anthropic"
                      />
                    </label>

                    <label className="field-label">
                      Anthropic Model
                      <input
                        className="field-input"
                        type="text"
                        value={anthropicModel}
                        onChange={(event) => setAnthropicModel(event.target.value)}
                        placeholder="例如：claude-3-5-sonnet-20241022"
                      />
                    </label>

                    <label className="field-label">
                      Anthropic API Key
                      <input
                        className="field-input"
                        type="password"
                        autoComplete="new-password"
                        value={anthropicApiKey}
                        onChange={(event) => {
                          setAnthropicApiKey(event.target.value);
                          if (clearAnthropicApiKey && event.target.value.trim()) {
                            setClearAnthropicApiKey(false);
                          }
                        }}
                        placeholder={
                          settingsQuery.data?.anthropic_api_key_configured
                            ? "已配置，如需更新请输入新的 Key"
                            : "输入要保存的 Anthropic API Key"
                        }
                      />
                    </label>

                    <label className="settings-inline-toggle">
                      <input
                        type="checkbox"
                        checked={clearAnthropicApiKey}
                        onChange={(event) => {
                          setClearAnthropicApiKey(event.target.checked);
                          if (event.target.checked) {
                            setAnthropicApiKey("");
                          }
                        }}
                      />
                      保存时清除已保存的 Anthropic API Key
                    </label>
                  </>
                )}

                {saveMutation.isError ? (
                  <div className="management-error-banner">{saveMutation.error.message}</div>
                ) : null}
                {saveMutation.isSuccess ? (
                  <div className="management-inline-notice">配置已保存。</div>
                ) : null}

                <div className="management-action-row">
                  <button
                    className="button button-primary"
                    type="submit"
                    disabled={saveMutation.isPending}
                  >
                    {saveMutation.isPending ? "保存中" : "保存配置"}
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={() => {
                      if (!settingsQuery.data) {
                        return;
                      }

                      setProvider(settingsQuery.data.provider);
                      setBaseUrl(settingsQuery.data.base_url ?? "");
                      setModel(settingsQuery.data.model ?? "");
                      setApiKey("");
                      setClearApiKey(false);
                      setAnthropicBaseUrl(settingsQuery.data.anthropic_base_url ?? "");
                      setAnthropicModel(settingsQuery.data.anthropic_model ?? "");
                      setAnthropicApiKey("");
                      setClearAnthropicApiKey(false);
                    }}
                    disabled={saveMutation.isPending}
                  >
                    重置表单
                  </button>
                </div>
              </form>
            </section>
          </div>
        )}
      </section>
    </main>
  );
}
