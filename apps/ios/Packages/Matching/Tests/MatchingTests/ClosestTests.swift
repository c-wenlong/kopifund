import Foundation
import Testing
@testable import Matching

/// G24: an alert that still fails says which pattern came closest and the
/// field it could not read, by that field's before and after words.
/// Invented wallets, merchants and references only.

private let arrived = Calendar.current.date(
    from: DateComponents(year: 2026, month: 10, day: 20, hour: 9))!

private let wallet = AlertPattern(
    name: "Demo Wallet", direction: .outflow, accountID: "",
    segments: [.literal("You paid "), .amount, .literal("\nTo: "), .payee, .literal("\nRef")],
    sampleText: "Wallet payment\nYou paid SGD 4.20\nTo: KOPI CORNER\nRef A1001")

private let card = AlertPattern(
    name: "Demo Card", direction: .outflow, accountID: "",
    segments: [.literal("charged"), .amount, .literal("at"), .payee],
    sampleText: "You charged SGD5.00 at KOPITIAM")

private func closest(_ text: String, _ patterns: [AlertPattern] = [wallet, card],
                     source: String? = nil) -> ClosestPattern? {
    Matcher.closest(text: text, source: source, receivedAt: arrived, patterns: patterns)
}

@Test func aMissingPayeeLabelNamesTheFieldAndItsAnchor() {
    let text = "Wallet payment\nYou paid SGD 9.10\nPaid to: LAMP SHOP\nRef E5005"
    let c = closest(text)
    #expect(c?.patternName == "Demo Wallet")
    #expect(c?.problem == "payee not found after 'To:'")
    #expect(c?.summary == "Closest: Demo Wallet: payee not found after 'To:'")
    #expect(c?.fieldsRead == 1)
}

@Test func aMissingFirstAnchorNamesTheFirstFieldButIsNoClosest() {
    let text = "Wallet payment\nYou sent SGD 9.10\nTo: LAMP SHOP\nRef E5005"
    let e = Matcher.explain(wallet, text: text, source: nil, receivedAt: arrived,
                            zone: .current, arrivedOn: nil)
    #expect(e?.0 == "amount not found after 'You paid'")
    #expect(e?.1 == 0)
    // a pattern that reads none of an alert is no nearer than any other
    #expect(closest(text, [wallet]) == nil)
}

@Test func aMissingAfterAnchorNamesTheTextAfterTheField() {
    let c = closest("Wallet payment\nYou paid SGD 9.10\nTo: LAMP SHOP\nReceipt E5005", [wallet])
    #expect(c?.problem == "payee not found before 'Ref'")
    // the payee's own words read; only its end is missing
    #expect(c?.fieldsRead == 2)
}

@Test func thePatternThatReadsFurthestWins() {
    // the card pattern reads nothing of a wallet mail; the wallet one reads
    // its amount before it stops
    let c = closest("Wallet payment\nYou paid SGD 9.10\nPayee LAMP SHOP\nRef E5005")
    #expect(c?.patternID == wallet.id)
}

@Test func anAlertNoPatternTouchesHasNoClosest() {
    #expect(closest("Your sign-in code is 482913. It expires in 10 minutes.") == nil)
    #expect(closest("Wallet payment", []) == nil)
}

@Test func anAlertThatReadsHasNoClosest() {
    #expect(closest("Wallet payment\nYou paid SGD 9.10\nTo: LAMP SHOP\nRef E5005") == nil)
}

@Test func aValueTheShapeReadsButRefusesSaysWhy() {
    var sourced = wallet
    sourced.sourceMatch = "wallet-mail"
    let text = "Wallet payment\nYou paid SGD 9.10\nTo: LAMP SHOP\nRef E5005"
    #expect(closest(text, [sourced], source: "sms")?.problem == #"source "sms" lacks "wallet-mail""#)
    #expect(closest("Wallet payment\nYou paid KWD 9.105\nTo: LAMP SHOP\nRef E5005", [wallet])?
        .problem == "amount KWD 9.105 has 3 decimals; the app keeps cents")
}

// MARK: - restoring a saved pattern into the builder

@Test func aSavedPatternsTagsComeBackOnItsSample() throws {
    let text = "Wallet payment\nYou paid SGD 4.20\nTo: KOPI CORNER\nRef A1001"
    let tokens = Tokenizer.tokenize(text)
    let built = try PatternBuilder.segments(
        in: text, tokens: tokens,
        spans: TagDraft.detected(in: text, tokens: tokens).spans(tokens: tokens))
    let saved = AlertPattern(name: "Demo Wallet", direction: .outflow, accountID: "",
                             segments: built, sampleText: text)

    let draft = try #require(TagDraft.restored(from: saved, tokens: tokens))
    let rebuilt = try PatternBuilder.segments(in: text, tokens: tokens, spans: draft.spans(tokens: tokens))
    #expect(rebuilt == saved.segments)
    #expect(TagDraft.anchorEdits(from: saved, rebuilt: rebuilt).isEmpty)
}

@Test func anEditedBorderComesBackAsAnAnchorEdit() throws {
    let text = "Wallet payment\nYou paid SGD 4.20\nTo: KOPI CORNER\nRef A1001"
    let tokens = Tokenizer.tokenize(text)
    let fresh = try PatternBuilder.segments(
        in: text, tokens: tokens,
        spans: TagDraft.detected(in: text, tokens: tokens).spans(tokens: tokens))
    let edit = PatternAnchors.Edit(field: .amount, side: .before, text: "paid")
    let saved = AlertPattern(name: "Demo Wallet", direction: .outflow, accountID: "",
                             segments: try PatternAnchors.applying([edit], to: fresh),
                             sampleText: text)

    let draft = try #require(TagDraft.restored(from: saved, tokens: tokens))
    let rebuilt = try PatternBuilder.segments(in: text, tokens: tokens, spans: draft.spans(tokens: tokens))
    let edits = TagDraft.anchorEdits(from: saved, rebuilt: rebuilt)
    #expect(try PatternAnchors.applying(edits, to: rebuilt) == saved.segments)
}


@Test func theLiveTestSaysWhereItStops() {
    #expect(Matcher.whyNot(wallet, text: "Wallet payment\nYou paid SGD 9.10\nPaid to: LAMP SHOP\nRef E5005")
            == "payee not found after 'To:'")
    #expect(Matcher.whyNot(wallet, text: "Wallet payment\nYou paid SGD 9.10\nTo: LAMP SHOP\nRef E5005") == nil)
}

// MARK: - review fixes

private func tagged(_ text: String, _ field: (String) -> PatternBuilder.Field?)
    -> (tokens: [Token], tags: [Int: PatternBuilder.Field]) {
    let tokens = Tokenizer.tokenize(text)
    var tags: [Int: PatternBuilder.Field] = [:]
    for (i, t) in tokens.enumerated() where t.kind != .whitespace {
        if let f = field(t.text) { tags[i] = f }
    }
    return (tokens, tags)
}

@Test func anIgnoredStretchSurvivesReopeningThePattern() throws {
    // two anchors split by an ignored reference: tags cannot carry the
    // ignore, so the saved segments stay the pattern until a re-tag
    let text = "Paid SGD 4.20 ref A1001 To: SHOP"
    let (tokens, tags) = tagged(text) {
        switch $0 {
        case "SGD", "4.20": return .amount
        case "A1001": return .ignore
        case "SHOP": return .payee
        default: return nil
        }
    }
    let segs = try PatternBuilder.segments(in: text, tokens: tokens,
                                           spans: PatternBuilder.spans(from: tags, tokens: tokens))
    #expect(segs.contains(.ignore))
    let saved = AlertPattern(name: "Demo Shop", direction: .outflow, accountID: "",
                             segments: segs, sampleText: text)

    let r = TagDraft.restoring(saved, tokens: tokens)
    #expect(r.kept == segs)
    #expect(r.edits.isEmpty)
    #expect(try r.base(for: r.draft.tags, text: text, tokens: tokens) == segs)
    // a re-tag rebuilds from the sample
    var retagged = r.draft.tags
    retagged[tokens.firstIndex { $0.text == "A1001" }!] = .ignore
    #expect(try r.base(for: retagged, text: text, tokens: tokens) == segs)
}

@Test func bordersEditedPastTheSampleSurviveReopening() throws {
    // the owner changed "To:" to "Paid to:" for a newer alert, so the
    // pattern no longer reads its own sample
    let text = "Wallet payment\nYou paid SGD 4.20\nTo: KOPI CORNER\nRef A1001"
    let tokens = Tokenizer.tokenize(text)
    let fresh = try PatternBuilder.segments(
        in: text, tokens: tokens,
        spans: TagDraft.detected(in: text, tokens: tokens).spans(tokens: tokens))
    let segs = try PatternAnchors.applying(
        [.init(field: .payee, side: .before, text: "Paid to: ")], to: fresh)
    let saved = AlertPattern(name: "Demo Wallet", direction: .outflow, accountID: "",
                             segments: segs, sampleText: text)
    #expect(TagDraft.restored(from: saved, tokens: tokens) == nil)

    let r = TagDraft.restoring(saved, tokens: tokens)
    #expect(r.kept == segs)
    #expect(try r.base(for: r.draft.tags, text: text, tokens: tokens) == segs)
}

@Test func anExactlyRestoredPatternKeepsNothingAside() throws {
    let text = "Wallet payment\nYou paid SGD 4.20\nTo: KOPI CORNER\nRef A1001"
    let tokens = Tokenizer.tokenize(text)
    let built = try PatternBuilder.segments(
        in: text, tokens: tokens,
        spans: TagDraft.detected(in: text, tokens: tokens).spans(tokens: tokens))
    let saved = AlertPattern(name: "Demo Wallet", direction: .outflow, accountID: "",
                             segments: built, sampleText: text)
    #expect(TagDraft.restoring(saved, tokens: tokens).kept == nil)
}

@Test func aLeadingIgnoreIsNoProgress() {
    // the builder opens a pattern on an ignore when it drops a greeting;
    // that alone reads any text and must not make the pattern "closest"
    var greeted = wallet
    greeted.segments = [.ignore] + wallet.segments
    #expect(closest("Your sign-in code is 482913. It expires in 10 minutes.", [greeted]) == nil)
}

@Test func moreFieldsReadBeatsMoreAnchorsRead() {
    // `wordy` reads three anchors and no field; `wallet` reads its amount
    let wordy = AlertPattern(
        name: "Demo Wordy", direction: .outflow, accountID: "", priority: 5,
        segments: [.literal("Wallet"), .ignore, .literal("payment"), .ignore, .literal("You"),
                   .ignore, .literal("Paid to:"), .payee, .literal("Ref"), .amount],
        sampleText: "")
    let c = closest("Wallet payment\nYou paid SGD 9.10\nTo LAMP SHOP\nRef E5005", [wordy, wallet])
    #expect(c?.patternID == wallet.id)
}

@Test func theLiveTestIgnoresTheSourceFilter() {
    var sourced = wallet
    sourced.sourceMatch = "wallet-mail"
    #expect(Matcher.whyNot(sourced, text: "Wallet payment\nYou paid KWD 9.105\nTo: LAMP SHOP\nRef E5005")
            == "amount KWD 9.105 has 3 decimals; the app keeps cents")
}

@Test func theLiveTestReadsAYearlessDateOnTheDayItArrived() throws {
    let dated = AlertPattern(
        name: "Demo Dated", direction: .outflow, accountID: "",
        segments: [.literal("On "), .date(.ddMmm), .literal(" you paid "), .amount,
                   .literal(" to "), .payee, .literal("\nEnd")],
        sampleText: "On 12 Oct you paid SGD 4.20 to KOPI CORNER\nEnd")
    let text = "On 29 Feb you paid SGD 9.10 to LAMP SHOP\nEnd"
    let leap = try #require(CalendarDate(year: 2028, month: 3, day: 2))
    let plain = try #require(CalendarDate(year: 2027, month: 3, day: 2))
    #expect(Matcher.whyNot(dated, text: text, arrivedOn: leap) == nil)
    #expect(Matcher.whyNot(dated, text: text, arrivedOn: plain) == "date not readable")
}
