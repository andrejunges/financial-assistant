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
import {
  CreatedTransaction,
  formatBrl,
  FundingSource,
  runCli,
  Suggestion,
} from "./utils";

type FormValues = {
  entry: string;
  suggestion: string;
  fundingSource: string;
  tags: string;
  date: Date | null;
};

export default function Command() {
  const [entry, setEntry] = useState("");
  const [fundingSources, setFundingSources] = useState<FundingSource[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selectedSuggestion, setSelectedSuggestion] = useState("");
  const [selectedFundingSource, setSelectedFundingSource] = useState("");
  const [selectedDate, setSelectedDate] = useState<Date | null>(new Date());
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();

  useEffect(() => {
    async function loadAccounts() {
      setIsLoading(true);
      setError(undefined);
      try {
        const nextSources = await runCli<FundingSource[]>(["funding-sources"]);
        setFundingSources(nextSources);
        const defaultSource =
          nextSources.find((source) => source.default) ?? nextSources[0];
        if (defaultSource) {
          setSelectedFundingSource(defaultSource.value);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setIsLoading(false);
      }
    }

    loadAccounts();
  }, []);

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

  const selectedSource = useMemo(
    () =>
      fundingSources.find((source) => source.value === selectedFundingSource),
    [fundingSources, selectedFundingSource],
  );

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

    const sourceValue = values.fundingSource || selectedFundingSource;
    const source =
      fundingSources.find((candidate) => candidate.value === sourceValue) ??
      selectedSource;
    if (!source) {
      await showToast({
        style: Toast.Style.Failure,
        title: "No account or card selected",
      });
      return;
    }

    const tags = (values.tags ?? "")
      .split(",")
      .map((tag) => tag.trim().replace(/^#/, ""))
      .filter(Boolean);
    const date = values.date ?? selectedDate ?? new Date();
    const finalPayload = {
      ...payload,
      amount_cents: -Math.abs(payload.amount_cents),
      account_id: source.id,
      account_name: source.name,
      credit_card_id: source.kind === "credit_card" ? source.id : undefined,
      source_kind: source.kind,
      date: toDateString(date),
      tags,
    };

    const confirmed = await confirmAlert({
      title: "Create expense?",
      message: [
        `${finalPayload.description} · ${formatBrl(finalPayload.amount_cents)} · ${finalPayload.account_name}`,
        finalPayload.date,
        tags.length ? `Tags: ${tags.join(", ")}` : "",
      ]
        .filter(Boolean)
        .join("\n"),
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
        JSON.stringify(finalPayload),
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
      <Form.Dropdown
        id="fundingSource"
        title="Account or Card"
        value={selectedFundingSource}
        onChange={setSelectedFundingSource}
        error={error}
      >
        {fundingSources.map((source) => (
          <Form.Dropdown.Item
            key={source.value}
            value={source.value}
            title={source.name}
            icon={
              source.default
                ? Icon.Star
                : source.kind === "credit_card"
                  ? Icon.CreditCard
                  : Icon.Coins
            }
            keywords={[source.kind === "credit_card" ? "cartão" : "conta"]}
          />
        ))}
      </Form.Dropdown>
      <Form.DatePicker
        id="date"
        title="Date"
        type={Form.DatePicker.Type.Date}
        value={selectedDate}
        onChange={setSelectedDate}
      />
      <Form.TextField
        id="tags"
        title="Tags"
        placeholder="trabalho, reembolso"
      />
      {selected ? (
        <Form.Description
          title="Will Create"
          text={`${selected.description}\n${formatBrl(selected.amount_cents)} · ${selectedSource?.name ?? selected.account_name} · ${toDateString(selectedDate ?? new Date())}`}
        />
      ) : null}
    </Form>
  );
}

function toDateString(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
