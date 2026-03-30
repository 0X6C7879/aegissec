export type SkillCompatibilitySource = "local" | "claude" | "opencode" | "agents" | (string & {});

export type SkillCompatibilityScope = "project" | "user" | (string & {});

export type SkillRecordStatus = "loaded" | "invalid" | "ignored" | (string & {});

export type SkillRecord = {
  id: string;
  source: SkillCompatibilitySource;
  scope: SkillCompatibilityScope;
  root_dir: string;
  directory_name: string;
  entry_file: string;
  name: string;
  description: string;
  compatibility: string[];
  metadata: Record<string, unknown>;
  raw_frontmatter: Record<string, unknown>;
  status: SkillRecordStatus;
  error_message: string | null;
  content_hash: string;
  last_scanned_at: string;
};
