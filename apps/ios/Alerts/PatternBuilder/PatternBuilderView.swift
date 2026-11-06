import SwiftUI
import SwiftData
import Storage
import Matching
import os

/// "Open builder" in Instruments' os_signpost track: from the first
/// tokenize to the first laid-out document, so a slow open on a long alert
/// is a number, not a feeling.
private let signposter = OSSignposter(subsystem: "com.kopifund.alerts", category: "PatternBuilder")

/// G24: the key screen, rebuilt around confirmation. The alert stays a
/// readable document; detected fields arrive pre-tagged, weaker guesses stay
/// dashed; a field chip arms the next tap, and a live "what it reads" card
/// shows the extraction before the pattern saves. The tagging itself is
/// `TagDraft` (Matching); the document, the dock and the form are their own
/// views in this folder.
struct PatternBuilderView: View {
    let sampleText: String
    let source: String?
    /// The day a saved alert arrived, so the preview reads its yearless
    /// date as intake will on save; nil for a new sample (read as today).
    var arrivedOn: CalendarDate? = nil
    var onSaved: (() -> Void)? = nil

    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @AppStorage("planID") private var planID = ""
    @State private var draft = TagDraft()
    @State private var selected: PatternBuilder.Field = .amount
    @State private var dimmed: Set<Int> = []
    @State private var name = ""
    @State private var nameTouched = false
    // the last name the payee span wrote: an edit only counts as the user's
    // when the text leaves it, so the name keeps following a re-tag
    @State private var autoName = ""
    @State private var direction: AlertPattern.Direction = .outflow
    @State private var accountID = ""
    @State private var defaultCategoryID = ""
    @State private var sourceMatch = ""
    @State private var priority = 0
    @State private var autoApprove = false
    @State private var testText = ""
    @State private var buildError = ""
    @State private var testResult = ""
    @State private var matched: ParsedTransaction?
    @State private var accountOptions: [(String, String)] = []
    @State private var categoryOptions: [(String, String)] = []
    // keeps a store refresh from re-keying the tab under this draft
    @State private var draftHold = SyncService.DraftHold()

    // tokenized once per alert text, not per access: as a computed property
    // every render re-tokenized the alert once per word, so a 4 KB email
    // froze for many seconds on open and on every tap
    @State private var tokens: [Token] = []
    @State private var preparedText: String?
    @State private var openInterval: OSSignpostIntervalState?
    private var spans: [PatternBuilder.Span] { draft.spans(tokens: tokens) }
    private var built: AlertPattern? { try? builtPattern() }

    /// The work that depends only on the alert text runs once per text.
    /// `onAppear` fires again on every return to the tab, and re-running
    /// detection there would restore pre-tags the user had removed.
    private func prepare() {
        guard preparedText != sampleText else { return }
        preparedText = sampleText
        openInterval = signposter.beginInterval("Open builder", id: signposter.makeSignpostID(),
                                                "\(sampleText.count) chars")
        tokens = Tokenizer.tokenize(sampleText)
        // on open the confident guesses land as tags and the rest stay dashed
        draft = TagDraft.detected(in: sampleText, tokens: tokens)
        selected = TagDraft.nextMissing(in: spans) ?? .amount
        dimmed = AlertText.boilerplateTokenIndexes(in: sampleText, tokens: tokens)
        // nothing tappable lays out no atom, so no frame would end the
        // interval: a blank paste ends it here
        if !tokens.contains(where: \.isTappable) { endOpenInterval(atoms: 0) }
    }

    private func endOpenInterval(atoms: Int) {
        guard let open = openInterval else { return }
        signposter.endInterval("Open builder", open, "\(atoms) atoms")
        openInterval = nil
    }

    private func spanText(for indexes: [Int]) -> String {
        guard let lo = indexes.min(), let hi = indexes.max() else { return "" }
        return String(sampleText[tokens[lo].range.lowerBound..<tokens[hi].range.upperBound])
    }

    private var foundCount: Int {
        let have = Set(spans.map(\.field))
        return TagDraft.required.filter { have.contains($0) }.count
    }

    // MARK: - body

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                TokenDocument(text: sampleText, tokens: tokens, dimmed: dimmed, armed: selected,
                              draft: $draft) { endOpenInterval(atoms: $0) }
                captureCard
                PatternMetaForm(name: $name, direction: $direction, accountID: $accountID,
                                defaultCategoryID: $defaultCategoryID, sourceMatch: $sourceMatch,
                                priority: $priority, autoApprove: $autoApprove,
                                accountOptions: accountOptions, categoryOptions: categoryOptions) {
                    if name != autoName { nameTouched = true }
                }
                testSection
            }
            .padding()
        }
        .navigationTitle("New pattern")
        .toolbar { progressBadge }
        // the floating tab bar stacked under the dock and the Save button
        // and together they hid a quarter of the screen; the builder is a
        // task with its own way out, so the tabs step aside
        .toolbar(.hidden, for: .tabBar)
        .safeAreaInset(edge: .bottom) {
            BuilderDock(spans: spans, armed: $selected, blocking: canSave ? nil : blockingText,
                        onSave: save)
        }
        .onChange(of: draft.tags) {
            // a re-tagged payee renames the pattern too, until the name
            // field itself has been edited
            if !nameTouched, let s = spans.first(where: { $0.field == .payee }) {
                autoName = spanText(for: s.tokenIndexes)
                name = autoName
            }
            runTest()
        }
        .onChange(of: testText) { runTest() }
        .onChange(of: sampleText) { prepare() }
        .onAppear {
            prepare()
            loadAccounts()
            loadCategories()
            if sourceMatch.isEmpty, let s = source { sourceMatch = s }
        }
    }

    private var progressBadge: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Text("\(foundCount) of 3 found")
                .font(.caption.bold()).monospacedDigit()
                .padding(.horizontal, 9).padding(.vertical, 4)
                .background(foundCount == 3 ? Color.green.opacity(0.16) : Color.orange.opacity(0.16))
                .foregroundStyle(foundCount == 3 ? .green : .orange)
                .clipShape(Capsule())
        }
    }

    // MARK: capture card ("this pattern reads")

    /// One card, only when there is something to say: the matched summary,
    /// the reason it cannot read the alert, and the live test's verdict.
    /// A good sample does not hide what the pasted alert did.
    @ViewBuilder
    private var captureCard: some View {
        if matched != nil || !buildError.isEmpty || !testResult.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                if let m = matched {
                    Text("READS").font(.caption2.bold()).foregroundStyle(.green)
                    Text(readsSummary(m)).font(.callout)
                }
                if !buildError.isEmpty {
                    Text(buildError).font(.caption).foregroundStyle(.orange)
                }
                if !testResult.isEmpty {
                    Text(testResult).font(.caption)
                        .foregroundStyle(testResult.contains("no match") || testResult.contains("does not read")
                                         ? Color.red : Color.secondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
    }

    /// The card reads from the real match, not the tagged text: trimming,
    /// currency fallback and date parsing are exactly what intake produces.
    private func readsSummary(_ m: ParsedTransaction) -> String {
        var parts = [m.payee]
        let figure = String(format: "%.2f", (m.amount as NSDecimalNumber).doubleValue)
        parts.append((m.currency.map { "\($0) " } ?? "") + figure)
        parts.append(m.date.formatted(date: .abbreviated, time: .omitted))
        if let h = m.accountHint { parts.append("card \(h)") }
        parts.append("→ \(accountName)")
        return parts.joined(separator: " · ")
    }

    private var accountName: String {
        accountOptions.first { $0.0 == accountID }?.1 ?? "pick an account"
    }

    private var testSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Live test").font(.caption).foregroundStyle(.secondary)
            TextField("Paste another alert to try the pattern on it", text: $testText, axis: .vertical)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.callout)
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    // MARK: - mechanics

    private var canSave: Bool { built != nil && !name.isEmpty && !accountID.isEmpty }

    private var blockingText: String {
        if built == nil {
            return buildError.isEmpty ? "tag an amount and a payee to save" : buildError
        }
        if name.isEmpty { return "name it to save" }
        if accountID.isEmpty {
            return accountOptions.isEmpty ? "fetch accounts in Settings to save" : "pick an account to save"
        }
        return ""
    }

    private func builtPattern() throws -> AlertPattern {
        // the builder refuses a field tagged twice and an unreadable date
        // span with a BuildError, shown below
        let segs = try PatternBuilder.segments(in: sampleText, tokens: tokens, spans: spans)
        return AlertPattern(name: name.isEmpty ? "Untitled" : name, direction: direction,
                            accountID: accountID,
                            defaultCategoryID: defaultCategoryID.isEmpty ? nil : defaultCategoryID,
                            sourceMatch: sourceMatch.isEmpty ? nil : sourceMatch,
                            priority: priority, autoApprove: autoApprove,
                            segments: segs, sampleText: sampleText)
    }

    /// What the builder says when the tags cannot become a pattern.
    private static func refusal(_ error: Error) -> String {
        (error as? PatternBuilder.BuildError)?.message ?? "tag at least an amount and a payee"
    }

    private func runTest() {
        do {
            let p = try builtPattern()
            matched = nil
            testResult = ""
            // index, not content, says which alert is the live one: the
            // sample pasted verbatim into Live test must still report there
            for (i, text) in ([sampleText] + (testText.isEmpty ? [] : [testText])).enumerated() {
                let live = i > 0
                switch Matcher.test(p, text: text, arrivedOn: live ? nil : arrivedOn) {
                case .success(let t):
                    if live {
                        testResult = "test alert reads \(t.payee) · \(t.amount) · \(t.date.formatted(date: .abbreviated, time: .omitted))"
                    } else {
                        matched = t
                    }
                case .failure(let e):
                    testResult = live ? "test alert: no match, \(e.reason)"
                                      : "the pattern does not read this alert, \(e.reason)"
                }
            }
            buildError = ""
        } catch {
            buildError = Self.refusal(error)
            matched = nil
            testResult = ""
        }
    }

    private func loadAccounts() {
        accountOptions = YNABCache.accounts(in: context, planID: planID).map { ($0.id, $0.name) }
        if accountID.isEmpty {
            accountID = UserDefaults.standard.string(forKey: "defaultAccountID") ?? ""
        }
    }

    private func loadCategories() {
        categoryOptions = YNABCache.categories(in: context, planID: planID)
            .map { ($0.id, "\($0.group) · \($0.name)") }
    }

    private func save() {
        do {
            context.insert(PatternRecord(try builtPattern()))
            try context.save()
            onSaved?()
            dismiss()
        } catch {
            buildError = Self.refusal(error)
        }
    }
}
