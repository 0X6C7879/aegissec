/**
 * WooYun Legacy plugin for OpenCode
 *
 * Injects lightweight routing guidance so the native `wooyun-legacy`
 * skill is used proactively for security testing and business-logic review.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const WooyunLegacyPlugin = async () => {
  const homeDir = os.homedir();
  const configDir = path.join(homeDir, '.config', 'opencode');
  const skillDir = path.resolve(__dirname, '..', '..');
  const skillPath = path.join(skillDir, 'SKILL.md');
  const skillPathDisplay = path.join(configDir, 'skills', 'wooyun-legacy', 'SKILL.md');
  const description = 'Use when doing security testing, security audits, bug bounty triage, or business-logic review of web apps, APIs, or business systems.';

  const getBootstrap = () => {
    if (!fs.existsSync(skillPath)) return null;

    return `<IMPORTANT>
OpenCode plugin "wooyun-legacy" is installed.
A native skill is available at ${skillPathDisplay}.

Use the skill tool to load or apply \`wooyun-legacy\` for:
- security testing, security audits, bug bounty work, and code review for web apps or APIs
- auth, authorization, IDOR, payment, order, refund, race-condition, parameter-tampering, and business-logic analysis
- implicit black-box requests such as \"test this endpoint\", \"find bugs\", \"can I bypass this\", \"帮我测测这个接口\", and \"这个参数能不能改\"

Use progressive loading inside the skill:
- start with \`references/\`
- read \`knowledge/\` when deeper technique detail is needed
- read \`categories/\`, \`examples/\`, or \`evals/\` only when concrete cases, industry playbooks, or evidence are needed

Skill discovery description: ${description}
</IMPORTANT>`;
  };

  return {
    name: 'wooyun-legacy',
    'experimental.chat.system.transform': async (_input, output) => {
      const bootstrap = getBootstrap();
      if (bootstrap) {
        (output.system ||= []).push(bootstrap);
      }
    },
  };
};
