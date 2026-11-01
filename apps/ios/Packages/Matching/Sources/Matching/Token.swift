import Foundation

/// G24: one atom of an alert text. Words, numbers and each punctuation mark are
/// tokens; whitespace is a token of its own that the builder does not offer
/// for tagging. `range` is the token's span inside the original string.
public struct Token: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case word, number, punct, whitespace
    }
    public let text: String
    public let kind: Kind
    public let range: Range<String.Index>
    /// Tokens the builder lets the user tag. Whitespace joins literals only.
    public var isTappable: Bool { kind != .whitespace }
}

public enum Tokenizer {
    /// Split an alert into tappable atoms: `[0-9][0-9,.]*` stays one number
    /// token, `[A-Za-z]+` one word, every other character a punct token.
    public static func tokenize(_ text: String) -> [Token] {
        var tokens: [Token] = []
        var i = text.startIndex
        while i < text.endIndex {
            let c = text[i]
            var j = text.index(after: i)
            let kind: Token.Kind
            if c.isNumber {
                // a "." or "," belongs to the number only when a digit follows,
                // so "2026." ends the number at the sentence's full stop
                while j < text.endIndex {
                    let d = text[j]
                    if d.isNumber {
                        j = text.index(after: j)
                    } else if d == "." || d == "," {
                        let k = text.index(after: j)
                        guard k < text.endIndex, text[k].isNumber else { break }
                        j = k
                    } else {
                        break
                    }
                }
                kind = .number
            } else if c.isLetter {
                // letters may trail digits so references like "AB12" stay one token
                while j < text.endIndex, text[j].isLetter || text[j].isNumber {
                    j = text.index(after: j)
                }
                kind = .word
            } else if c.isWhitespace {
                while j < text.endIndex, text[j].isWhitespace {
                    j = text.index(after: j)
                }
                kind = .whitespace
            } else {
                kind = .punct
            }
            tokens.append(Token(text: String(text[i..<j]), kind: kind, range: i..<j))
            i = j
        }
        return tokens
    }
}
