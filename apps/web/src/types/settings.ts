export type ModelProvider = "openai" | "anthropic";

export type ModelApiSettings = {
  provider: ModelProvider;
  base_url: string | null;
  model: string | null;
  api_key_configured: boolean;
  anthropic_base_url: string | null;
  anthropic_model: string | null;
  anthropic_api_key_configured: boolean;
};

export type ModelApiSettingsUpdate = {
  provider: ModelProvider | null;
  base_url: string | null;
  model: string | null;
  api_key: string | null;
  clear_api_key: boolean;
  anthropic_base_url: string | null;
  anthropic_model: string | null;
  anthropic_api_key: string | null;
  clear_anthropic_api_key: boolean;
};
