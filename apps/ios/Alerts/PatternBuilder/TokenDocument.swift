import SwiftUI
import Matching

/// The alert as a readable document the owner tags: a tap tags or untags a
/// word, a sideways stroke tags every word it covers with the armed field,
/// and a tagged run renders as one tinted span. The tags themselves are the
/// builder's `TagDraft`; this view owns only the stroke in progress.
struct TokenDocument: View {
    let text: String
    let tokens: [Token]
    /// Token indexes inside boilerplate lines, rendered quieter.
    let dimmed: Set<Int>
    let armed: PatternBuilder.Field
    @Binding var draft: TagDraft
    /// The first layout with any atom: the builder ends its open signpost.
    var onLaidOut: (Int) -> Void = { _ in }

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

    private var atoms: [TagDraft.Atom] {
        TagDraft.atoms(in: text, tokens: tokens, spans: draft.spans(tokens: tokens))
    }

    var body: some View {
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
                        .background(inDrag ? armed.tint : field.tint)
                        .clipShape(RoundedRectangle(cornerRadius: 5))
                        .background(atomFrame(indexes.first ?? pos))
                        .onTapGesture { draft.tapSpan(indexes, field: field, armed: armed) }
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
            if !frames.isEmpty { onLaidOut(frames.count) }
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

    /// The token range of the atom a point sits on: one index for a bare
    /// word, the whole span for a merged run. Gaps and margins report no
    /// frame and are skipped over.
    private func atomRange(at p: CGPoint) -> ClosedRange<Int>? {
        guard let t = atomFrames.first(where: { $0.value.contains(p) })?.key else { return nil }
        return TagDraft.tokenRange(containing: t, in: atoms)
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
            preDragTags = draft.tags
            dragStartRange = r
            dragIsSelecting = true
        }
        guard let s = dragStartRange, let e = atomRange(at: p) else { return }
        dragTokens = min(s.lowerBound, e.lowerBound)...max(s.upperBound, e.upperBound)
    }

    /// The selection commits once, when the finger lifts.
    private func dragEnd() {
        if dragIsSelecting, let r = dragTokens {
            draft.commitStroke(r, armed: armed, onto: preDragTags, tokens: tokens)
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
        if let sug = draft.suggestions[i] {
            Text(t.text)
                .font(dim ? .caption : .callout)
                .foregroundStyle(dim ? Color(.tertiaryLabel) : sug.color)
                .underline(true, pattern: .dash, color: sug.color)
                .padding(.vertical, 2)
                .background(inDrag ? armed.tint : Color.clear)
                .onTapGesture { draft.tap(i, armed: armed) }
        } else {
            Text(t.text)
                .font(dim ? .caption : .callout)
                .foregroundStyle(dim ? Color(.tertiaryLabel) : Color(.label))
                .padding(.vertical, 2)
                .background(inDrag ? armed.tint : Color.clear)
                .contentShape(Rectangle())
                .onTapGesture { draft.tap(i, armed: armed) }
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
