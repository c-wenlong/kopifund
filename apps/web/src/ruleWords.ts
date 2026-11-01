// G13: the words the Rules page uses for what the rule files call tiers. The
// ids (key, type, flow, merchant, ...) are the files' own and stay what the
// server expects; these are what a person reads.

// what a rule matches on, as a short label
export const TIER_LABEL: Record<string, string> = {
  key: "Single row",
  type: "Bank type",
  flow: "Money in or out",
  merchant: "Merchant text",
  counterparty: "Other party",
  memo: "Memo",
  reference: "Reference",
};

// the same, as "tested on ..." for the pattern's own label
export const TIER_TESTS: Record<string, string> = {
  key: "one row",
  type: "the bank's own label for the payment",
  flow: "whether money moved in, out or between your accounts",
  merchant: "the merchant text on the statement",
  counterparty: "the other party's name",
  memo: "the memo",
  reference: "the reference",
};

export const tierLabel = (tier: string) => TIER_LABEL[tier] ?? tier;
