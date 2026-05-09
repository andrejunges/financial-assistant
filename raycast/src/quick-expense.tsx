import {
  Action,
  ActionPanel,
  confirmAlert,
  Form,
  Icon,
  popToRoot,
  showToast,
  Toast,
} from "@raycast/api";
import { useEffect, useMemo, useState } from "react";
import { CreatedTransaction, formatBrl, runCli, Suggestion } from "./utils";

type FormValues = {
  entry: string;
  suggestion: string;
};

export default function Command() {
  const [entry, setEntry] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selectedSuggestion, setSelectedSuggestion] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();

  useEffect(() => {
    const trimmed = entry.trim();
    if (!trimmed) {
      setSuggestions([]);
      setSelectedSuggestion("");
      setError(undefined);
      return;
    }

    const timeout = setTimeout(async () => {
      setIsLoading(true);
      setError(undefined);
      try {
        const nextSuggestions = await runCli<Suggestion[]>([
          "suggest",
          trimmed,
          "--limit",
          "5",
        ]);
        setSuggestions(nextSuggestions);
        setSelectedSuggestion(
          nextSuggestions[0] ? JSON.stringify(nextSuggestions[0]) : "",
        );
      } catch (e) {
        setSuggestions([]);
        setSelectedSuggestion("");
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setIsLoading(false);
      }
    }, 250);

    return () => clearTimeout(timeout);
  }, [entry]);

  const selected = useMemo(() => {
    if (!selectedSuggestion) {
      return undefined;
    }
    try {
      return JSON.parse(selectedSuggestion) as Suggestion;
    } catch {
      return undefined;
    }
  }, [selectedSuggestion]);

  async function submit(values: FormValues) {
    const payload =
      selected ??
      (values.suggestion
        ? (JSON.parse(values.suggestion) as Suggestion)
        : undefined);
    if (!payload) {
      await showToast({
        style: Toast.Style.Failure,
        title: "No suggestion selected",
      });
      return;
    }

    const confirmed = await confirmAlert({
      title: "Create expense?",
      message: `${payload.description} · ${formatBrl(payload.amount_cents)} · ${payload.account_name}`,
      primaryAction: {
        title: "Create",
      },
    });
    if (!confirmed) {
      return;
    }

    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Creating expense",
    });
    try {
      const created = await runCli<CreatedTransaction>([
        "create",
        "--payload",
        JSON.stringify(payload),
      ]);
      toast.style = Toast.Style.Success;
      toast.title = "Expense created";
      toast.message = `${created.description} · ${formatBrl(Math.round(created.amount_brl * 100))}`;
      await popToRoot();
    } catch (e) {
      toast.style = Toast.Style.Failure;
      toast.title = "Could not create expense";
      toast.message = e instanceof Error ? e.message : String(e);
    }
  }

  return (
    <Form
      isLoading={isLoading}
      actions={
        <ActionPanel>
          <Action.SubmitForm
            icon={Icon.CheckCircle}
            title="Create Expense"
            onSubmit={submit}
          />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="entry"
        title="Quick Entry"
        placeholder="Bancarela 57"
        value={entry}
        onChange={setEntry}
        autoFocus
      />
      <Form.Dropdown
        id="suggestion"
        title="Suggestion"
        value={selectedSuggestion}
        onChange={setSelectedSuggestion}
        error={error}
      >
        {suggestions.map((suggestion) => (
          <Form.Dropdown.Item
            key={`${suggestion.description}-${suggestion.account_id}-${suggestion.category_id ?? "none"}`}
            value={JSON.stringify(suggestion)}
            title={`${suggestion.description} · ${formatBrl(suggestion.amount_cents)}`}
            icon={Icon.Receipt}
            keywords={[suggestion.input_description, suggestion.account_name]}
          />
        ))}
      </Form.Dropdown>
      {selected ? (
        <Form.Description
          title="Will Create"
          text={`${selected.description}\n${formatBrl(selected.amount_cents)} · ${selected.account_name} · ${selected.date}`}
        />
      ) : null}
    </Form>
  );
}
