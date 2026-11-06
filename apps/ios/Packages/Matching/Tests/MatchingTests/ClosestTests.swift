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
