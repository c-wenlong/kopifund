import SwiftUI
import Matching

/// The builder's bottom dock: what to tag next, one chip per field (the
/// armed one picks what the next tap tags), and Save, which names what
/// still blocks it.
struct BuilderDock: View {
    let spans: [PatternBuilder.Span]
    @Binding var armed: PatternBuilder.Field
    /// nil when the pattern can save; otherwise what blocks it.
    let blocking: String?
    let onSave: () -> Void

    var body: some View {
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
            Button { onSave() } label: {
                Text(blocking ?? "Save pattern").font(.headline)
                    .frame(maxWidth: .infinity).padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent).controlSize(.large)
            .disabled(blocking != nil)
        }
        .padding(.horizontal).padding(.top, 10)
        .background(.regularMaterial)
    }

    private func has(_ f: PatternBuilder.Field) -> Bool { spans.contains { $0.field == f } }

    private var hintText: String {
        if !has(.amount) { return "Tap or drag over the amount, the money figure" }
        if !has(.payee) { return "Now the payee, the merchant or person" }
        if !has(.date) { return "And the date, when it happened" }
        return ""
    }

    private func chip(_ f: PatternBuilder.Field) -> some View {
        let count = spans.filter { $0.field == f }.count
        let needed = TagDraft.required.contains(f) && count == 0
        return Button { armed = f } label: {
            VStack(spacing: 3) {
                Circle().fill(f.color)
                    .overlay(needed ? Circle().stroke(f.color.opacity(0.5), lineWidth: 4) : nil)
                    .frame(width: 8, height: 8)
                Text(f.label).font(.caption2.weight(.semibold))
                Text(count > 0 ? "\(count)" : "·").font(.caption2).monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity).padding(.vertical, 8)
            .background(armed == f ? Color(.tertiarySystemFill) : Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11)
                .strokeBorder(armed == f ? Color(.separator) : Color(.separator).opacity(0.4)))
        }
        .foregroundStyle(.primary)
    }
}
