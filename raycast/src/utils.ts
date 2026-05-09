import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

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

export type CreatedTransaction = {
  id: number;
  description: string;
  amount_brl: number;
  date: string;
  account: string;
  category: string;
};

function cliPath() {
  return path.resolve(process.cwd(), "..", "finance_cli.py");
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
    timeout: 30000,
    maxBuffer: 1024 * 1024,
  });
  return JSON.parse(stdout) as T;
}
