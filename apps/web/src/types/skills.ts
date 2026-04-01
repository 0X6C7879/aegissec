export type SkillCompatibilitySource = "local" | "claude" | "opencode" | "agents" | (string & {});

export type SkillCompatibilityScope = "project" | "user" | (string & {});

export type SkillRecordStatus = "loaded" | "invalid" | "ignored" | (string & {});

export type SkillParameterSchema = Record<string, unknown>;

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
  parameter_schema: SkillParameterSchema;
  raw_frontmatter: Record<string, unknown>;
  status: SkillRecordStatus;
  enabled: boolean;
  error_message: string | null;
  content_hash: string;
  last_scanned_at: string;
};

export type SkillContent = {
  id: string;
  name: string;
  directory_name: string;
  entry_file: string;
  parameter_schema: SkillParameterSchema;
  content: string;
};

export type SkillContextSkill = {
  id: string;
  name: string;
  directory_name: string;
  description: string;
  compatibility: string[];
  parameter_schema: SkillParameterSchema;
};

export type SkillContext = {
  payload: {
    skills: SkillContextSkill[];
    [key: string]: unknown;
  };
  prompt_fragment: string;
};
