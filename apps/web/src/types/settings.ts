export type ModelApiSettings = {
  base_url: string | null;
  model: string | null;
  api_key_configured: boolean;
};

export type ModelApiSettingsUpdate = {
  base_url: string | null;
  model: string | null;
  api_key: string | null;
  clear_api_key: boolean;
};
