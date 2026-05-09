import { showToast, Toast } from "@raycast/api";
import { runCli } from "./utils";

type RefreshResult = {
  transactions_cached: number;
};

export default async function Command() {
  const toast = await showToast({
    style: Toast.Style.Animated,
    title: "Refreshing suggestions",
  });
  try {
    const result = await runCli<RefreshResult>([
      "refresh-templates",
      "--days",
      "180",
    ]);
    toast.style = Toast.Style.Success;
    toast.title = "Suggestions refreshed";
    toast.message = `${result.transactions_cached} transactions scanned`;
  } catch (e) {
    toast.style = Toast.Style.Failure;
    toast.title = "Could not refresh suggestions";
    toast.message = e instanceof Error ? e.message : String(e);
  }
}
