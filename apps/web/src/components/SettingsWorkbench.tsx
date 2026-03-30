import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getModelApiSettings, updateModelApiSettings } from "../lib/api";
import type { ModelApiSettingsUpdate } from "../types/settings";

const MODEL_API_SETTINGS_QUERY_KEY = ["settings", "model-api"] as const;

function normalizeInputValue(value: string): string | null {
  const trimmedValue = value.trim();
  return trimmedValue.length > 0 ? trimmedValue : null;
}

export function SettingsWorkbench() {
  const queryClient = useQueryClient();
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);

  const settingsQuery = useQuery({
    queryKey: MODEL_API_SETTINGS_QUERY_KEY,
    queryFn: ({ signal }) => getModelApiSettings(signal),
  });

  useEffect(() => {
    if (!settingsQuery.data) {
      return;
    }

    setBaseUrl(settingsQuery.data.base_url ?? "");
    setModel(settingsQuery.data.model ?? "");
    setApiKey("");
    setClearApiKey(false);
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: ModelApiSettingsUpdate) => updateModelApiSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: MODEL_API_SETTINGS_QUERY_KEY });
      setApiKey("");
      setClearApiKey(false);
    },
  });

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    await saveMutation.mutateAsync({
      base_url: normalizeInputValue(baseUrl),
      model: normalizeInputValue(model),
      api_key: clearApiKey ? null : normalizeInputValue(apiKey),
      clear_api_key: clearApiKey,
    });
  }

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="模型 API 设置">
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
          <>
            <div className="management-unified-body management-unified-stack">
              <section className="management-section-card">
                <div className="management-section-header">
                  <h3 className="management-section-title">配置</h3>
                </div>

                <form className="settings-form" onSubmit={(event) => void handleSubmit(event)}>
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

                  {saveMutation.isError ? <div className="management-error-banner">{saveMutation.error.message}</div> : null}
                  {saveMutation.isSuccess ? <div className="management-inline-notice">配置已保存。</div> : null}

                  <div className="management-action-row">
                    <button className="button button-primary" type="submit" disabled={saveMutation.isPending}>
                      {saveMutation.isPending ? "保存中" : "保存配置"}
                    </button>
                    <button
                      className="button button-secondary"
                      type="button"
                      onClick={() => {
                        if (!settingsQuery.data) {
                          return;
                        }

                        setBaseUrl(settingsQuery.data.base_url ?? "");
                        setModel(settingsQuery.data.model ?? "");
                        setApiKey("");
                        setClearApiKey(false);
                      }}
                      disabled={saveMutation.isPending}
                    >
                      重置表单
                    </button>
                  </div>
                </form>
              </section>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
