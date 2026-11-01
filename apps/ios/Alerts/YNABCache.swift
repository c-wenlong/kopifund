import Foundation
import SwiftData
import Storage

/// G24: the YNAB pickers' cached choices, decoded the same way everywhere a
/// view needs them: the pattern builder's account and category pickers, and
/// the list captions that show an account's name instead of its id.
struct AccountStub: Codable { let id: String; let name: String }
struct CategoryStub: Codable { let id: String; let name: String; let group: String }

enum YNABCache {
    /// `planID` nil merges every cached plan's accounts; a caption that only
    /// knows an account id needs the union, while the builder's picker wants
    /// just the plan the pattern will post to.
    static func accounts(in context: ModelContext, planID: String? = nil) -> [AccountStub] {
        let rows = (try? context.fetch(FetchDescriptor<MetadataRecord>())) ?? []
        var seen = Set<String>()
        var out: [AccountStub] = []
        for row in rows where row.key.hasPrefix("accounts|") {
            if let planID, row.key != "accounts|\(planID)" { continue }
            for a in (try? JSONDecoder().decode([AccountStub].self, from: row.payload)) ?? []
            where seen.insert(a.id).inserted {
                out.append(a)
            }
        }
        return out
    }

    static func categories(in context: ModelContext, planID: String) -> [CategoryStub] {
        let rows = (try? context.fetch(FetchDescriptor<MetadataRecord>())) ?? []
        guard let row = rows.first(where: { $0.key == "categories|\(planID)" }),
              let list = try? JSONDecoder().decode([CategoryStub].self, from: row.payload)
        else { return [] }
        return list
    }

    static func accountName(for id: String, in context: ModelContext) -> String? {
        accounts(in: context).first { $0.id == id }?.name
    }
}
