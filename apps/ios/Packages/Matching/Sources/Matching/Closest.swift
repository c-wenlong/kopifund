import Foundation

/// G24: why an alert still sits in Unmatched, said as the one field the
/// nearest pattern could not read: "Closest: Demo Wallet: payee not found
/// after 'To:'". The pattern is read one field at a time, by the words
/// before and after each field (`PatternAnchors`), so the first field
/// whose border the alert lacks is the one to fix.
public struct ClosestPattern: Equatable, Sendable {
    public var patternID: UUID
    public var patternName: String
    /// What it could not read: "payee not found after 'To:'".
    public var problem: String
    /// Fields read before it stopped, the measure of "closest".
    public var fieldsRead: Int

    /// The line Unmatched's alert page shows.
    public var summary: String { "Closest: \(patternName): \(problem)" }
}

extension Matcher {

    /// The saved pattern that reads the most fields of `text` before it
    /// stops, and where it stopped; then the one that reads the most
    /// anchors, then the higher priority, as matching ranks. Ignored
    /// stretches are no progress: one matches any text, so a pattern the
    /// builder opened with one would otherwise come "closest" to anything.
    /// Nil when no pattern reads even its first anchor, or one reads the
    /// whole alert (then it is no miss).
    public static func closest(text: String, source: String?, receivedAt: Date,
                               zone: TimeZone = .current, arrivedOn: CalendarDate? = nil,
                               patterns: [AlertPattern]) -> ClosestPattern? {
        var best: ClosestPattern?
        var bestRank = (fields: 0, anchors: 0)
        for p in patterns.sorted(by: { $0.priority > $1.priority }) {
            guard let (problem, depth, fields) = explain(p, text: text, source: source,
                                                         receivedAt: receivedAt, zone: zone,
                                                         arrivedOn: arrivedOn)
            // a pattern that reads the alert whole leaves nothing to explain
            else { return nil }
            let anchors = p.segments[..<depth].filter { $0 != .ignore }.count
            guard anchors > 0 else { continue }
            if fields > bestRank.fields || (fields == bestRank.fields && anchors > bestRank.anchors) {
                bestRank = (fields, anchors)
                best = ClosestPattern(patternID: p.id, patternName: p.name,
                                      problem: problem, fieldsRead: fields)
            }
        }
        return best
    }

    /// Why `p` does not read `text`, as the field it stops at ("payee not
    /// found after 'To:'"); nil when it reads it. The builder's live test
    /// says this instead of a bare "text does not match". A pasted text has
    /// no source, so the source filter is left out: it would hide the
    /// field's own refusal behind "source lacks".
    public static func whyNot(_ p: AlertPattern, text: String, receivedAt: Date = Date(),
                              zone: TimeZone = .current, arrivedOn: CalendarDate? = nil) -> String? {
        var textOnly = p
        textOnly.sourceMatch = nil
        return explain(textOnly, text: text, source: nil, receivedAt: receivedAt, zone: zone,
                       arrivedOn: arrivedOn)?.0
    }

    /// Where one pattern stops on `text`: the problem, how many segments
    /// read in order (0 when not even the first one did), and how many of
    /// them were fields. Nil when it reads the alert whole.
    static func explain(_ p: AlertPattern, text: String, source: String?, receivedAt: Date,
                        zone: TimeZone, arrivedOn: CalendarDate?) -> (String, Int, Int)? {
        let segs = p.segments
        // the longest run of segments, from the first, that still reads
        var depth = 0
        while depth < segs.count, reads(Array(segs[..<(depth + 1)]), in: text) { depth += 1 }
        let fields = segs[..<depth].filter { fieldName($0) != nil }.count
        if depth < segs.count {
            return (stop(at: depth, in: segs), depth, fields)
        }
        // the shape reads: the source filter or a field's value refused it
        if let s = p.sourceMatch, !s.isEmpty, !(source ?? "").localizedCaseInsensitiveContains(s) {
            return ("source \"\(source ?? "")\" lacks \"\(s)\"", depth, fields)
        }
        switch test(p, text: text, receivedAt: receivedAt, zone: zone, arrivedOn: arrivedOn) {
        case .success: return nil
        case .failure(let e): return (e.reason, depth, fields)
        }
    }

    /// The problem when segment `k` is the first that does not read. A
    /// field that does not read is "not found after" the words before it.
    /// Missing words name the field right beside them: the one they open
    /// ("not found after"), else the one they close ("not found before").
    /// Only a direct neighbour counts: across an ignored stretch or other
    /// words the field has borders of its own, and naming it would point
    /// the owner at the wrong edit.
    private static func stop(at k: Int, in segs: [AlertPattern.Segment]) -> String {
        if let field = fieldName(segs[k]) {
            let before = k > 0 ? anchorText(segs[k - 1], side: .before) : nil
            return before.map { "\(field) not found after '\($0)'" } ?? "\(field) not found"
        }
        if k + 1 < segs.count, let next = fieldName(segs[k + 1]),
           let words = anchorText(segs[k], side: .before) {
            return "\(next) not found after '\(words)'"
        }
        if k > 0, let prev = fieldName(segs[k - 1]),
           let words = anchorText(segs[k], side: .after) {
            return "\(prev) not found before '\(words)'"
        }
        if let words = anchorText(segs[k], side: .before) { return "'\(words)' not found" }
        return "text does not match"
    }

    /// The segments read somewhere in `text`, as the matcher reads them.
    private static func reads(_ segments: [AlertPattern.Segment], in text: String) -> Bool {
        // a prefix ending on a literal border: a trailing ignore adds nothing
        let p = AlertPattern(name: "", direction: .outflow, accountID: "",
                             segments: segments, sampleText: "")
        guard let re = try? cachedExpression(for: p) else { return false }
        if case .found = searchFromEachStart(p, re, in: text) { return true }
        return false
    }

    private static func fieldName(_ s: AlertPattern.Segment) -> String? {
        switch s {
        case .amount: return "amount"
        case .payee: return "payee"
        case .date: return "date"
        case .accountHint: return "card"
        case .literal, .ignore: return nil
        }
    }

    /// A literal as the words nearest the field: the last line of the text
    /// before it, the first line of the text after it. Nil for no words.
    private static func anchorText(_ s: AlertPattern.Segment, side: PatternAnchors.Side) -> String? {
        guard case .literal(let lit) = s else { return nil }
        let lines = lit.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return side == .before ? lines.last : lines.first
    }
}

extension TagDraft {
    /// G24: a saved pattern's tags, back on its own sample so the builder
    /// can edit it: each field's capture on the sample tags the tokens it
    /// covers. Nil when the pattern no longer reads its sample.
    public static func restored(from p: AlertPattern, tokens: [Token]) -> TagDraft? {
        guard let re = try? Matcher.cachedExpression(for: p),
              case .found(let m) = Matcher.searchFromEachStart(p, re, in: p.sampleText)
        else { return nil }
        var groups: [(String, Field)] = [("amount", .amount), ("payee", .payee),
                                         ("accountHint", .account)]
        let dates = p.segments.filter { if case .date = $0 { return true }; return false }.count
        for k in 0..<dates { groups.append(("date\(k)", .date)) }
        var d = TagDraft()
        for (name, field) in groups {
            let r = m.range(withName: name)
            guard r.location != NSNotFound,
                  let range = Range(r, in: p.sampleText) else { continue }
            for (i, t) in tokens.enumerated() where t.kind != .whitespace
                && t.range.overlaps(range) {
                d.tags[i] = field
            }
        }
        return d
    }

    /// A saved pattern back in the builder: the tags to show, the anchor
    /// edits on top of their build, and, when those cannot rebuild the
    /// pattern exactly, the saved segments themselves. Tags cannot carry
    /// an ignored stretch the owner tagged, and a pattern whose borders
    /// were edited to read a newer alert may no longer read its own sample
    /// (then the tags are the detector's). Either way `kept` holds the
    /// saved segments, which stay the pattern while the tags are untouched,
    /// so an edit of the name or a setting never changes what it reads.
    public static func restoring(_ p: AlertPattern, tokens: [Token]) -> RestoredPattern {
        let detected = TagDraft.detected(in: p.sampleText, tokens: tokens)
        var draft = detected
        if let r = restored(from: p, tokens: tokens) {
            draft.tags = r.tags
            for i in r.tags.keys { draft.suggestions[i] = nil }
            if let rebuilt = try? PatternBuilder.segments(in: p.sampleText, tokens: tokens,
                                                          spans: draft.spans(tokens: tokens)) {
                let edits = anchorEdits(from: p, rebuilt: rebuilt)
                if (try? PatternAnchors.applying(edits, to: rebuilt)) == p.segments {
                    return RestoredPattern(draft: draft, edits: edits, kept: nil)
                }
            }
        }
        return RestoredPattern(draft: draft, edits: [], kept: p.segments)
    }

    /// The anchor edits that turn the tags' fresh build back into `p`'s
    /// segments, for a pattern whose borders the owner changed.
    public static func anchorEdits(from p: AlertPattern, rebuilt: [AlertPattern.Segment])
        -> [PatternAnchors.Edit] {
        let want = PatternAnchors.anchors(in: p.segments)
        let have = PatternAnchors.anchors(in: rebuilt)
        var edits: [PatternAnchors.Edit] = []
        for w in want {
            guard let h = have.first(where: { $0.field == w.field }) else { continue }
            if w.before != h.before {
                edits.append(.init(field: w.field, side: .before, text: w.before ?? ""))
            }
            if w.after != h.after {
                edits.append(.init(field: w.field, side: .after, text: w.after ?? ""))
            }
        }
        return edits
    }
}

/// G24: a saved pattern reopened in the builder (`TagDraft.restoring`).
public struct RestoredPattern: Equatable, Sendable {
    /// The tags and suggestions the builder opens on.
    public var draft: TagDraft
    /// Anchor edits on top of the tags' build.
    public var edits: [PatternAnchors.Edit]
    /// The saved segments, when the tags cannot rebuild them exactly.
    public var kept: [AlertPattern.Segment]?

    /// What the anchor edits apply to: the kept segments while `tags` are
    /// still the ones restored, else what the tags build. A re-tag is the
    /// owner rebuilding the pattern from the sample.
    public func base(for tags: [Int: PatternBuilder.Field], text: String,
                     tokens: [Token]) throws -> [AlertPattern.Segment] {
        if let kept, tags == draft.tags { return kept }
        return try PatternBuilder.segments(in: text, tokens: tokens,
                                           spans: PatternBuilder.spans(from: tags, tokens: tokens))
    }
}
