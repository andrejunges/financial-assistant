import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { getPreferenceValues } from "@raycast/api";

const execFileAsync = promisify(execFile);

type Preferences = {
  repoPath: string;
};

export type Suggestion = {
  description: string;
  input_description: string;
  amount_cents: number;
  date: string;
  account_id: number;
  account_name: string;
  category_id: number | null;
  score: number;
};

export type FundingSource = {
  id: number;
  kind: "account" | "credit_card";
  value: string;
  name: string;
  default: boolean;
};

export type CreatedTransaction = {
  id: number;
  description: string;
  amount_brl: number;
  date: string;
  account: string;
  category: string;
};

function cliPath() {
  const preferences = getPreferenceValues<Preferences>();
  return path.join(preferences.repoPath, "finance_cli.py");
}

export function formatBrl(amountCents: number) {
  const abs = Math.abs(amountCents) / 100;
  const prefix = amountCents < 0 ? "-" : "+";
  return `${prefix}R$ ${abs.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export async function runCli<T>(args: string[]) {
  const { stdout } = await execFileAsync("python3", [cliPath(), ...args], {
    cwd: getPreferenceValues<Preferences>().repoPath,
    timeout: 30000,
    maxBuffer: 1024 * 1024,
  });
  return JSON.parse(stdout) as T;
}
