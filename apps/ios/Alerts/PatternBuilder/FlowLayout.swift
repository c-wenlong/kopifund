import SwiftUI

/// The layout the token document wraps in: views flow left to right and wrap
/// like text; a subview tagged `FlowBreak` forces a new line.
struct FlowBreak: LayoutValueKey {
    static var defaultValue: Bool { false }
}

struct FlowLayout: Layout {
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
