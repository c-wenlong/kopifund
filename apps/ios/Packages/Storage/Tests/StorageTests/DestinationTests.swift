import Testing
@testable import Storage

/// G24: Save, Approve and the row say the same thing about where an
/// approved row goes.
struct DestinationTests {
    @Test func everyCombinationSaysWhereRowsGo() {
        #expect(Destination.summary(linked: true, ynab: true) == "Approved rows go to Kopifund and YNAB")
        #expect(Destination.summary(linked: true, ynab: false) == "Approved rows go to Kopifund")
        #expect(Destination.summary(linked: false, ynab: true) == "Approved rows go to YNAB")
        #expect(Destination.summary(linked: false, ynab: false)
                == "Waiting for Kopifund: link this phone in Settings")
    }

    @Test func anApprovedRowWithNowhereToGoSaysItWaits() {
        #expect(Destination.note(state: "approved", linked: false, ynab: false, sentToKopifund: false)
                == Destination.waiting)
        #expect(Destination.note(state: "approved", linked: true, ynab: false, sentToKopifund: false)
                == "sending to Kopifund")
        #expect(Destination.note(state: "approved", linked: true, ynab: false, sentToKopifund: true)
                == "in Kopifund")
    }

    @Test func withYNABTheUsualStatesStand() {
        #expect(Destination.note(state: "approved", linked: false, ynab: true, sentToKopifund: false) == nil)
        #expect(Destination.note(state: "pending", linked: false, ynab: false, sentToKopifund: false) == nil)
        #expect(Destination.note(state: "synced", linked: true, ynab: true, sentToKopifund: true) == nil)
    }
}
