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
/// shows the extraction before the pattern saves.
struct PatternBuilderView: View {
    let sampleText: String
    let source: String?
    var onSaved: (() -> Void)? = nil

    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @AppStorage("planID") private var planID = ""
    @State private var tags: [Int: PatternBuilder.Field] = [:]
    @State private var suggestions: [Int: PatternBuilder.Field] = [:]
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
    @State private var accountOptions: [(String, String)] = []
    @State private var categoryOptions: [(String, String)] = []

    // tokenized once per alert text, not per access: as a computed property
    // every render re-tokenized the alert once per word, so a 4 KB email
    // froze for many seconds on open and on every tap
    @State private var tokens: [Token] = []
    @State private var preparedText: String?
    @State private var openInterval: OSSignpostIntervalState?
    private var spans: [PatternBuilder.Span] { PatternBuilder.spans(from: tags, tokens: tokens) }
    private var built: AlertPattern? { try? builtPattern() }

    // MARK: - atoms: what the flow renders

    /// A contiguous tagged run renders as one unit; whitespace either keeps
    /// a line (gap) or breaks it (newline); everything else is a tappable word.
    private enum Atom: Identifiable {
        case token(Int)
        case span(PatternBuilder.Field, String, [Int])
        case gap(Int)
        case newline(Int, Int)
        var id: String {
            switch self {
            case .token(let i): return "t\(i)"
            case .gap(let i): return "g\(i)"
            case .newline(let i, let k): return "n\(i)-\(k)"
            case .span(_, _, let idx): return "s\(idx.first ?? -1)"
            }
        }
    }

    private var atoms: [Atom] {
        let byFirst = Dictionary(uniqueKeysWithValues: spans.map { ($0.firstIndex, $0) })
        // a span's whitespace is inside its rendered text, so the whole range
        // must be skipped, not just the tagged word tokens
        var covered = Set<Int>()
        for s in spans { covered.formUnion(s.firstIndex...s.lastIndex) }
        var out: [Atom] = []
        for i in tokens.indices {
            if let s = byFirst[i] {
                out.append(.span(s.field, PatternBuilder.spanText(in: sampleText, tokens: tokens, span: s),
                                 s.tokenIndexes))
            } else if covered.contains(i) {
                continue
            } else if tokens[i].kind == .whitespace {
                let breaks = tokens[i].text.filter { $0 == "\n" }.count
                if breaks == 0 { out.append(.gap(i)) }
                else { for k in 0..<breaks { out.append(.newline(i, k)) } }
            } else {
                out.append(.token(i))
            }
        }
        return out
    }

    // MARK: - detection

    /// On open the confident guesses land as tags and the rest stay dashed.
    /// Every alternative of a tagged field remains a suggestion so a wrong
    /// guess is always one tap from being right.
    private func applyCandidates() {
        let cands = Candidates.detect(in: sampleText, tokens: tokens)
        // one confident guess per field becomes a pre-tag, ranked so "to
        // MERCHANT" beats "from BANK"; tokens an earlier candidate already
        // claimed stay with it ("ACME USD" must not eat "USD 4.80")
        for field in [PatternBuilder.Field.amount, .payee, .date] {
            if let c = cands.filter({ $0.field == field && $0.confident })
                .min(by: { $0.rank < $1.rank }) {
                for i in c.tokenIndexes where tags[i] == nil { tags[i] = c.field }
            }
        }
        for c in cands {
            for i in c.tokenIndexes where tags[i] == nil && suggestions[i] == nil {
                suggestions[i] = c.field
            }
        }
        selected = nextMissingField() ?? .amount
    }

    /// The work that depends only on the alert text runs once per text.
    /// `onAppear` fires again on every return to the tab, and re-running
    /// detection there would restore pre-tags the user had removed.
    private func prepare() {
        guard preparedText != sampleText else { return }
        preparedText = sampleText
        openInterval = signposter.beginInterval("Open builder", id: signposter.makeSignpostID(),
                                                "\(sampleText.count) chars")
        tokens = Tokenizer.tokenize(sampleText)
        tags = [:]
        suggestions = [:]
        applyCandidates()
        dimmed = AlertText.boilerplateTokenIndexes(in: sampleText, tokens: tokens)
        // nothing tappable lays out no atom, so no frame would end the
        // interval: a blank paste ends it here
        if !tokens.contains(where: \.isTappable), let open = openInterval {
            signposter.endInterval("Open builder", open, "0 atoms")
            openInterval = nil
        }
    }

    private func spanText(for indexes: [Int]) -> String {
        guard let lo = indexes.min(), let hi = indexes.max() else { return "" }
        return String(sampleText[tokens[lo].range.lowerBound..<tokens[hi].range.upperBound])
    }

    // MARK: - tagging

    private func tap(_ i: Int) {
        if tags[i] == selected {
            tags[i] = nil
        } else if tags[i] == nil, let sug = suggestions[i] {
            // a dashed guess accepts with one tap whatever field is armed
            tags[i] = sug
            suggestions[i] = nil
        } else {
            tags[i] = selected
            suggestions[i] = nil
        }
        // `selected` never moves on its own: payees are usually several
        // words, so the armed field stays until a chip is tapped
    }

    private func tapSpan(_ indexes: [Int], field: PatternBuilder.Field) {
        if field == selected { for i in indexes { tags[i] = nil } }
        else { for i in indexes { tags[i] = selected; suggestions[i] = nil } }
    }

    private func nextMissingField() -> PatternBuilder.Field? {
        let have = Set(spans.map(\.field))
        for f in [PatternBuilder.Field.amount, .payee, .date] where !have.contains(f) { return f }
        return nil
    }

    private var foundCount: Int {
        let have = Set(spans.map(\.field))
        return [PatternBuilder.Field.amount, .payee, .date].filter { have.contains($0) }.count
    }

    // MARK: - body

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                documentCard
                captureCard
                metaForm
                testSection
            }
            .padding()
        }
        .navigationTitle("New pattern")
        .toolbar { progressBadge }
        .safeAreaInset(edge: .bottom) { dock }
        .onChange(of: tags) {
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

    // MARK: document

    private var documentCard: some View {
        FlowLayout(hSpace: 0, vSpace: 3) {
            ForEach(Array(atoms.enumerated()), id: \.element.id) { pos, atom in
                switch atom {
                case .newline:
                    Color.clear.frame(width: 0, height: 0)
                        .layoutValue(key: FlowBreak.self, value: true)
                case .gap:
                    Text(" ").font(.callout)
                case .token(let i):
                    tokenView(i)
                        .background(atomFrame(i))
                case .span(let field, let text, let indexes):
                    // a tagged phrase inside the stroke previews the armed
                    // field too, so the visible range matches what commits
                    let inDrag = dragTokens.map { r in indexes.contains { r.contains($0) } } ?? false
                    Text(text)
                        .font(.callout)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(inDrag ? tint(selected) : tint(field))
                        .clipShape(RoundedRectangle(cornerRadius: 5))
                        .background(atomFrame(indexes.first ?? pos))
                        .onTapGesture { tapSpan(indexes, field: field) }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .coordinateSpace(name: "doc")
        .simultaneousGesture(dragToTag)
        .onPreferenceChange(AtomFramesKey.self) { frames in
            atomFrames = frames
            if !frames.isEmpty, let open = openInterval {
                signposter.endInterval("Open builder", open, "\(frames.count) atoms")
                openInterval = nil
            }
        }
    }

    /// Each atom reports its frame keyed by its FIRST token index. Token
    /// indexes never move while tagging merges atoms, so a frame captured
    /// before the merge still resolves to the right atom after it; the
    /// preference replaces the whole map on every layout, cleaning the
    /// entries of atoms a merge swallowed.
    private func atomFrame(_ firstToken: Int) -> some View {
        GeometryReader { g in
            Color.clear.preference(key: AtomFramesKey.self,
                                   value: [firstToken: g.frame(in: .named("doc"))])
        }
    }

    /// The first token index of whatever atom the point sits on. Gaps and
    /// margins report no frame and are skipped over.
    private func leadToken(at p: CGPoint) -> Int? {
        atomFrames.first { $0.value.contains(p) }?.key
    }

    /// The token range of the atom holding token `t`: one index for a bare
    /// word, the whole span for a merged run.
    private func atomTokenRange(containing t: Int) -> ClosedRange<Int>? {
        for atom in atoms {
            switch atom {
            case .token(let i) where i == t: return i...i
            case .span(_, _, let idx):
                if let lo = idx.first, let hi = idx.last, idx.contains(t) { return lo...hi }
            default: continue
            }
        }
        return nil
    }

    /// The token range of the atom a point sits on: one index for a bare
    /// word, the whole span for a merged run.
    private func atomRange(at p: CGPoint) -> ClosedRange<Int>? {
        guard let t = leadToken(at: p) else { return nil }
        return atomTokenRange(containing: t)
    }

    /// One gesture for the whole card: attaching it per atom meant the
    /// first tagged word replaced the .token view holding the touch with a
    /// .span view, killing the gesture mid-drag, so a stroke only ever
    /// marked its first word. Simultaneous keeps the page's scroll alive:
    /// `dragMove` only commits once the finger is clearly moving along the
    /// words, so a pull downwards keeps meaning "scroll the page".
    private var dragToTag: some Gesture {
        DragGesture(minimumDistance: 8, coordinateSpace: .named("doc"))
            .onChanged { dragMove(to: $0.location, start: $0.startLocation) }
            .onEnded { _ in dragEnd() }
    }

    /// The stroked range always covers the full bounds of both endpoint
    /// atoms, so the span the preview paints on overlap is exactly what the
    /// commit tags, whichever way the finger travels.
    private func dragMove(to p: CGPoint, start: CGPoint) {
        if !dragIsSelecting {
            let dx = abs(p.x - start.x), dy = abs(p.y - start.y)
            guard dx > 8, dx > dy * 1.2 else { return }
            guard let r = atomRange(at: start) else { return }
            preDragTags = tags
            dragStartRange = r
            dragIsSelecting = true
        }
        guard let s = dragStartRange, let e = atomRange(at: p) else { return }
        dragTokens = min(s.lowerBound, e.lowerBound)...max(s.upperBound, e.upperBound)
    }

    /// The selection commits once, when the finger lifts: every
    /// non-whitespace token in the last dragged range takes the armed field
    /// on top of the tags the drag found.
    private func dragEnd() {
        if dragIsSelecting, let r = dragTokens {
            var next = preDragTags
            for i in r where tokens[i].kind != .whitespace {
                next[i] = selected
                suggestions[i] = nil
            }
            tags = next
        }
        dragStartRange = nil
        dragIsSelecting = false
        dragTokens = nil
    }

    @ViewBuilder
    private func tokenView(_ i: Int) -> some View {
        let t = tokens[i]
        let dim = dimmed.contains(i)
        // inside the stroked range a word previews the armed field's tint;
        // it is not a tag yet, so the layout under the finger never shifts
        let inDrag = dragTokens?.contains(i) == true
        if let sug = suggestions[i] {
            Text(t.text)
                .font(dim ? .caption : .callout)
                .foregroundStyle(dim ? Color(.tertiaryLabel) : fieldColor(sug))
                .underline(true, pattern: .dash, color: fieldColor(sug))
                .padding(.vertical, 2)
                .background(inDrag ? tint(selected) : Color.clear)
                .onTapGesture { tap(i) }
        } else {
            Text(t.text)
                .font(dim ? .caption : .callout)
                .foregroundStyle(dim ? Color(.tertiaryLabel) : Color(.label))
                .padding(.vertical, 2)
                .background(inDrag ? tint(selected) : Color.clear)
                .contentShape(Rectangle())
                .onTapGesture { tap(i) }
        }
    }

    // MARK: capture card ("this pattern reads")

    /// One card, only when there is something to say: the matched summary,
    /// the reason it cannot read the alert, and the live test's verdict.
    /// A good sample does not hide what the pasted alert did.
    @ViewBuilder
    private var captureCard: some View {
        if matched != nil || !buildError.isEmpty || !testResult.isEmpty {
            captureShell {
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
            }
        }
    }

    private func captureShell<Content: View>(@ViewBuilder _ c: () -> Content) -> some View {
        c()
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 14))
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

    @State private var testResult = ""
    @State private var matched: ParsedTransaction?
    // a drag across words tags the whole run it covers: every token and
    // span atom reports its frame in the card's space, and the drag maps
    // its path back onto atom ordinals
    @State private var atomFrames: [Int: CGRect] = [:]
    @State private var dragStartRange: ClosedRange<Int>? = nil
    @State private var dragIsSelecting = false
    @State private var preDragTags: [Int: PatternBuilder.Field] = [:]
    // the in-progress selection is a highlight, not tags: committing mid-
    // drag would merge atoms and reflow the words under the moving finger.
    // Tags land in `dragEnd`, so the layout the drag hit-tests on is the
    // layout the user is actually looking at.
    @State private var dragTokens: ClosedRange<Int>? = nil

    // MARK: meta form

    private var metaForm: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("About this pattern").font(.caption).foregroundStyle(.secondary)
            VStack(spacing: 0) {
                fieldRow("Name") {
                    TextField("e.g. PayLah! Transfer", text: $name)
                        .onChange(of: name) { if name != autoName { nameTouched = true } }
                }
                divider
                fieldRow("Direction") {
                    Picker("Direction", selection: $direction) {
                        Text("Outflow").tag(AlertPattern.Direction.outflow)
                        Text("Inflow").tag(AlertPattern.Direction.inflow)
                    }.pickerStyle(.segmented)
                }
                divider
                fieldRow("Account") {
                    Picker("Account", selection: $accountID) {
                        if accountOptions.isEmpty {
                            Text("Fetch accounts in Settings first").tag("")
                        }
                        ForEach(accountOptions, id: \.0) { Text($0.1).tag($0.0) }
                    }.labelsHidden()
                }
            }
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            DisclosureGroup("More options") {
                VStack(spacing: 0) {
                    fieldRow("Category") {
                        if categoryOptions.isEmpty {
                            TextField("Optional", text: $defaultCategoryID)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                        } else {
                            Picker("Category", selection: $defaultCategoryID) {
                                Text("None").tag("")
                                ForEach(categoryOptions, id: \.0) { Text($0.1).tag($0.0) }
                            }.labelsHidden()
                        }
                    }
                    divider
                    fieldRow("Source has") {
                        TextField("Optional, e.g. email", text: $sourceMatch)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    divider
                    fieldRow("Priority") {
                        Stepper("", value: $priority).labelsHidden()
                        Text("\(priority)").font(.callout).monospacedDigit()
                    }
                    divider
                    fieldRow("") {
                        Toggle("Auto-approve matches", isOn: $autoApprove)
                            .font(.callout)
                    }
                }
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .padding(.top, 6)
            }
            .font(.callout)
        }
    }

    private var divider: some View {
        Divider().padding(.leading, 14)
    }

    private func fieldRow<Content: View>(_ k: String, @ViewBuilder _ c: () -> Content) -> some View {
        HStack(spacing: 12) {
            if !k.isEmpty {
                Text(k).font(.callout).foregroundStyle(.secondary).frame(width: 84, alignment: .leading)
            }
            c().font(.callout)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
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

    // MARK: dock

    private var dock: some View {
        VStack(spacing: 10) {
            if !hintText.isEmpty {
                Text(hintText)
                    .font(.footnote).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 7) {
                ForEach(PatternBuilder.Field.allCases, id: \.self) { f in
                    chip(f)
                }
            }
            Button { save() } label: {
                Text(canSave ? "Save pattern" : blockingText).font(.headline)
                    .frame(maxWidth: .infinity).padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .disabled(!canSave)
        }
        .padding(.horizontal).padding(.top, 10)
        .background(.regularMaterial)
    }

    private var hintText: String {
        if !hasField(.amount) { return "Tap or drag over the amount, the money figure" }
        if !hasField(.payee) { return "Now the payee, the merchant or person" }
        if !hasField(.date) { return "And the date, when it happened" }
        return ""
    }

    private func chip(_ f: PatternBuilder.Field) -> some View {
        let count = spans.filter { $0.field == f }.count
        let needed = [PatternBuilder.Field.amount, .payee, .date].contains(f) && count == 0
        return Button { selected = f } label: {
            VStack(spacing: 3) {
                Circle().fill(fieldColor(f))
                    .overlay(needed ? Circle().stroke(fieldColor(f).opacity(0.5), lineWidth: 4) : nil)
                    .frame(width: 8, height: 8)
                Text(label(of: f)).font(.caption2.weight(.semibold))
                Text(count > 0 ? "\(count)" : "·").font(.caption2).monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity).padding(.vertical, 8)
            .background(selected == f ? Color(.tertiarySystemFill) : Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11)
                .strokeBorder(selected == f ? Color(.separator) : Color(.separator).opacity(0.4)))
        }
        .foregroundStyle(.primary)
    }

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

    // MARK: - mechanics (same pipeline as before)

    private func hasField(_ f: PatternBuilder.Field) -> Bool { spans.contains { $0.field == f } }

    private func label(of f: PatternBuilder.Field) -> String {
        switch f {
        case .amount: return "Amount"
        case .payee: return "Payee"
        case .date: return "Date"
        case .account: return "Card"
        case .ignore: return "Ignore"
        }
    }

    private func fieldColor(_ f: PatternBuilder.Field) -> Color {
        switch f {
        case .amount: return .green
        case .payee: return .blue
        case .date: return .purple
        case .account: return .orange
        case .ignore: return .gray
        }
    }

    private func tint(_ f: PatternBuilder.Field) -> Color { fieldColor(f).opacity(0.25) }

    private func builtPattern() throws -> AlertPattern {
        let spans = PatternBuilder.spans(from: tags, tokens: tokens)
        // two "amount" spans would produce two captures the regex cannot read:
        // only `ignore` may repeat
        for f in PatternBuilder.Field.allCases where f != .ignore {
            if spans.filter({ $0.field == f }).count > 1 {
                throw PatternBuilder.BuildError.duplicateField(label(of: f).lowercased())
            }
        }
        // `segment(for:)` fatalErrors on a date it cannot read; checking here
        // keeps "tap a word as date" an error message, never a crash
        for s in spans where s.field == .date {
            let raw = PatternBuilder.spanText(in: sampleText, tokens: tokens, span: s)
                .trimmingCharacters(in: .whitespaces)
            if Dates.detectFormat(raw) == nil {
                throw PatternBuilder.BuildError.unreadableDate(raw)
            }
        }
        let segs = try PatternBuilder.segments(in: sampleText, tokens: tokens, spans: spans)
        return AlertPattern(name: name.isEmpty ? "Untitled" : name, direction: direction,
                            accountID: accountID,
                            defaultCategoryID: defaultCategoryID.isEmpty ? nil : defaultCategoryID,
                            sourceMatch: sourceMatch.isEmpty ? nil : sourceMatch,
                            priority: priority, autoApprove: autoApprove,
                            segments: segs, sampleText: sampleText)
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
                switch Matcher.test(p, text: text) {
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
        } catch let e as PatternBuilder.BuildError {
            switch e {
            case .missingField(let f): buildError = "tag an \(f) span"
            case .duplicateField(let f): buildError = "only one \(f) span per pattern"
            case .unreadableDate(let raw): buildError = #"date "\#(raw)" is not a format the app reads"#
            case .wildcardBesidePayee:
                buildError = "the payee needs a fixed word right beside it"
            case .wildcardBesideCard:
                buildError = "the card needs a fixed word right beside it"
            }
            matched = nil
            testResult = ""
        } catch {
            buildError = "tag at least an amount and a payee"
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
        } catch let e as PatternBuilder.BuildError {
            switch e {
            case .missingField(let f): buildError = "tag an \(f) span"
            case .duplicateField(let f): buildError = "only one \(f) span per pattern"
            case .unreadableDate(let raw): buildError = #"date "\#(raw)" is not a format the app reads"#
            case .wildcardBesidePayee:
                buildError = "the payee needs a fixed word right beside it"
            case .wildcardBesideCard:
                buildError = "the card needs a fixed word right beside it"
            }
        } catch {
            buildError = "tag at least an amount and a payee"
        }
    }

}

/// Each token and span atom reports its frame in the document's coordinate
/// space so a drag can map its path onto atoms.
private struct AtomFramesKey: PreferenceKey {
    static var defaultValue: [Int: CGRect] { [:] }
    static func reduce(value: inout [Int: CGRect], nextValue: () -> [Int: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { $1 })
    }
}

/// The layout the token document wraps in: views flow left to right and wrap
/// like text; a subview tagged `FlowBreak` forces a new line.
private struct FlowBreak: LayoutValueKey {
    static var defaultValue: Bool { false }
}

private struct FlowLayout: Layout {
    var hSpace: CGFloat = 0
    var vSpace: CGFloat = 3

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrange(subviews: subviews, width: proposal.width ?? 0).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let frames = arrange(subviews: subviews, width: bounds.width).frames
        for (i, f) in frames.enumerated() {
            subviews[i].place(at: CGPoint(x: bounds.minX + f.minX, y: bounds.minY + f.minY),
                              proposal: .unspecified)
        }
    }

    private func arrange(subviews: Subviews, width: CGFloat) -> (frames: [CGRect], size: CGSize) {
        var frames: [CGRect] = []
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0, maxW: CGFloat = 0
        for s in subviews {
            if s[FlowBreak.self] {
                x = 0; y += lineH + vSpace; lineH = 0
                frames.append(.zero)
                continue
            }
            var sz = s.sizeThatFits(.unspecified)
            // a span longer than the column wraps inside it instead of
            // running off the card's edge
            if sz.width > width {
                sz = s.sizeThatFits(ProposedViewSize(width: width, height: nil))
            }
            if x > 0, x + sz.width > width {
                x = 0; y += lineH + vSpace; lineH = 0
            }
            frames.append(CGRect(x: x, y: y, width: sz.width, height: sz.height))
            x += sz.width + hSpace
            lineH = max(lineH, sz.height)
            maxW = max(maxW, x)
        }
        return (frames, CGSize(width: maxW, height: y + lineH))
    }
}
