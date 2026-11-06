import Foundation
import SwiftData
import Storage
import YNABClient

/// G24: the YNAB pickers' cached choices, written by Settings' fetch and
/// decoded the same way everywhere a view needs them: the pattern builder's
/// account and category pickers, and the list captions that show an
/// account's name instead of its id.
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

    /// Settings' fetch for one plan: its accounts, for the pickers and the
    /// captions. Inserted, not saved; `save(categories:)` or the caller saves.
    static func save(accounts: [Account], planID: String, in context: ModelContext) {
        context.insert(MetadataRecord(key: "accounts|\(planID)",
                                      payload: (try? JSONEncoder().encode(accounts)) ?? Data(),
                                      fetchedAt: Date()))
    }

    /// The same fetch trip's categories, shown and not deleted, each under
    /// its group's name for the category picker.
    static func save(categories groups: [CategoryGroup], planID: String, in context: ModelContext) {
        let stubs = groups.filter { !$0.deleted && !$0.hidden }.flatMap { g in
            g.categories.filter { !$0.deleted && !$0.hidden }
                .map { CategoryStub(id: $0.id, name: $0.name, group: g.name) }
        }
        context.insert(MetadataRecord(key: "categories|\(planID)",
                                      payload: (try? JSONEncoder().encode(stubs)) ?? Data(),
                                      fetchedAt: Date()))
    }
}
