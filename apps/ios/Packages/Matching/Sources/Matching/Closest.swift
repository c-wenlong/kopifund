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

    /// The saved pattern that reads the most of `text` before it stops, and
    /// where it stopped. Ties go to the higher priority, as matching does.
    /// Nil when no pattern reads even its first anchor, or one reads the
    /// whole alert (then it is no miss).
    public static func closest(text: String, source: String?, receivedAt: Date,
                               zone: TimeZone = .current, arrivedOn: CalendarDate? = nil,
                               patterns: [AlertPattern]) -> ClosestPattern? {
        var best: ClosestPattern?
        var bestDepth = 0
        for p in patterns.sorted(by: { $0.priority > $1.priority }) {
            let (problem, depth, fields) = explain(p, text: text, source: source,
                                                   receivedAt: receivedAt, zone: zone,
                                                   arrivedOn: arrivedOn) ?? ("", 0, 0)
            // a pattern that reads the alert whole leaves nothing to explain
            if problem.isEmpty { return nil }
            if depth > bestDepth {
                bestDepth = depth
                best = ClosestPattern(patternID: p.id, patternName: p.name,
                                      problem: problem, fieldsRead: fields)
            }
        }
        return best
    }

    /// Why `p` does not read `text`, as the field it stops at ("payee not
    /// found after 'To:'"); nil when it reads it. The builder's live test
    /// says this instead of a bare "text does not match".
    public static func whyNot(_ p: AlertPattern, text: String, receivedAt: Date = Date(),
                              zone: TimeZone = .current) -> String? {
        explain(p, text: text, source: nil, receivedAt: receivedAt, zone: zone, arrivedOn: nil)?.0
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
    /// field that does not read is "not found after" the words before it;
    /// so is the next field when the words before it are what is missing.
    /// Words after the last field that are missing leave that field "not
    /// found before" them.
    private static func stop(at k: Int, in segs: [AlertPattern.Segment]) -> String {
        if let field = fieldName(segs[k]) {
            let before = k > 0 ? anchorText(segs[k - 1], side: .before) : nil
            return before.map { "\(field) not found after '\($0)'" } ?? "\(field) not found"
        }
        if let next = segs[(k + 1)...].first(where: { fieldName($0) != nil }).flatMap(fieldName),
           let words = anchorText(segs[k], side: .before) {
            return "\(next) not found after '\(words)'"
        }
        if let prev = segs[..<k].last(where: { fieldName($0) != nil }).flatMap(fieldName),
           let words = anchorText(segs[k], side: .after) {
            return "\(prev) not found before '\(words)'"
        }
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
        let ns = p.sampleText as NSString
        var groups: [(String, Field)] = [("amount", .amount), ("payee", .payee),
                                         ("accountHint", .account)]
        let dates = p.segments.filter { if case .date = $0 { return true }; return false }.count
        for k in 0..<dates { groups.append(("date\(k)", .date)) }
        var d = TagDraft()
        for (name, field) in groups {
            let r = m.range(withName: name)
            guard r.location != NSNotFound,
                  let range = Range(r, in: p.sampleText) else { continue }
            _ = ns
            for (i, t) in tokens.enumerated() where t.kind != .whitespace
                && t.range.overlaps(range) {
                d.tags[i] = field
            }
        }
        return d
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
