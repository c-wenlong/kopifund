import { accounts, comingSoon, findAccount, flag, type Bank } from "./accounts";

// what the API sends for a folder: its name and mark come from the layout
// registry (finance/account_names.py, G21), so the picker keeps no list of banks
const SENT: Record<string, [string, Bank]> = {
  "posb-everyday": ["POSB Everyday", "posb"], "dbs-paylah": ["DBS PayLah!", "paylah"], youtrip: ["YouTrip", "youtrip"],
  revolut: ["Revolut", "revolut"], "my-cu": ["My Cu", "other"], "dbs-multiplier": ["DBS Multiplier", "dbs"],
  "travel-card": ["Travel Card", "youtrip"], trust: ["Trust", "trust"], "uob-one": ["UOB One", "uob"],
};
const f = (name: string, parser: string, wallet = "") => {
  const slug = wallet && name.endsWith(`-${wallet.toLowerCase()}`) ? name.slice(0, -wallet.length - 1) : name;
  const [title, bank] = SENT[slug];
  return { path: `statements/${name}`, name, parser, wallet, title, bank };
};

describe("accounts", () => {
  it("folds currency wallets into one account and names each account", () => {
    const got = accounts([
      f("posb-everyday", "dbs"), f("dbs-paylah", "dbs"), f("youtrip-sek", "youtrip", "SEK"), f("youtrip-sgd", "youtrip", "SGD"),
      f("youtrip-aud", "youtrip", "AUD"), f("revolut", "revolut"), f("my-cu", "credit-union"),
    ]);
    expect(got.map((a) => [a.key, a.name, a.bank])).toEqual([
      ["statements/posb-everyday", "POSB Everyday", "posb"],
      ["statements/dbs-paylah", "DBS PayLah!", "paylah"],
      ["statements/youtrip", "YouTrip", "youtrip"],
      ["statements/revolut", "Revolut", "revolut"],
      ["statements/my-cu", "My Cu", "other"],
    ]);
    // SGD first, then the rest alphabetically; each keeps its own folder
    expect(got[2].wallets).toEqual([
      { currency: "SGD", path: "statements/youtrip-sgd" },
      { currency: "AUD", path: "statements/youtrip-aud" },
      { currency: "SEK", path: "statements/youtrip-sek" },
    ]);
    expect(got[0]).toMatchObject({ path: "statements/posb-everyday", wallets: [] });
  });

  it("takes a new bank's name and mark from the folder, with no list of its own (G21)", () => {
    expect(accounts([f("uob-one", "uob")]).map((a) => [a.name, a.bank])).toEqual([["UOB One", "uob"]]);
  });

  it("keeps a wallet folder whose slug does not end in its currency as its own account", () => {
    expect(accounts([f("travel-card", "youtrip", "EUR")])[0])
      .toMatchObject({ key: "statements/travel-card", wallets: [{ currency: "EUR", path: "statements/travel-card" }] });
  });

  it("keeps two accounts whose folders share a name apart", () => {
    const got = accounts([
      { path: "statements/dbs-multiplier", name: "dbs-multiplier", parser: "dbs", wallet: "", title: "DBS Multiplier", bank: "dbs" },
      { path: "statements/joint/dbs-multiplier", name: "dbs-multiplier", parser: "dbs", wallet: "", title: "DBS Multiplier", bank: "dbs" },
      { path: "statements/joint/youtrip-sgd", name: "youtrip-sgd", parser: "youtrip", wallet: "SGD", title: "YouTrip", bank: "youtrip" },
      { path: "statements/youtrip-sgd", name: "youtrip-sgd", parser: "youtrip", wallet: "SGD", title: "YouTrip", bank: "youtrip" },
    ]);
    expect(got.map((a) => [a.name, a.path || a.wallets[0].path])).toEqual([
      ["DBS Multiplier (statements)", "statements/dbs-multiplier"],
      ["DBS Multiplier (joint)", "statements/joint/dbs-multiplier"],
      ["YouTrip (joint)", "statements/joint/youtrip-sgd"],
      ["YouTrip (statements)", "statements/youtrip-sgd"],
    ]);
  });
});

describe("comingSoon", () => {
  it("lists Trust Bank until the config has a Trust folder", () => {
    expect(comingSoon(accounts([f("dbs-multiplier", "dbs")])).map((c) => c.name)).toEqual(["Trust Bank"]);
    expect(comingSoon(accounts([f("trust", "trust")]))).toEqual([]);
  });
});

describe("flag", () => {
  it("spells the country from the currency code", () => {
    expect(flag("SEK")).toBe("🇸🇪");
    expect(flag("EUR")).toBe("🇪🇺");
    expect(flag("myr")).toBe("🇲🇾");
    expect(flag("")).toBe("");
  });
});

describe("findAccount", () => {
  const list = accounts([f("dbs-multiplier", "dbs"), f("youtrip-sek", "youtrip", "SEK"), f("youtrip-sgd", "youtrip", "SGD")]);

  it("finds an account by its key, its folder name or its display name, in any case", () => {
    for (const want of ["statements/dbs-multiplier", "dbs-multiplier", "DBS Multiplier", "dbs multiplier"]) {
      expect(findAccount(list, want)?.account.name, want).toBe("DBS Multiplier");
    }
    expect(findAccount(list, "youtrip")).toEqual({ account: list[1], currency: "" });
  });

  it("reads a wallet folder as its account and its currency", () => {
    expect(findAccount(list, "youtrip-sek")).toEqual({ account: list[1], currency: "SEK" });
    expect(findAccount(list, "statements/youtrip-sgd")?.currency).toBe("SGD");
  });

  it("is null for a name nothing matches, or none", () => {
    expect(findAccount(list, "nowhere")).toBeNull();
    expect(findAccount(list, " ")).toBeNull();
  });
});
