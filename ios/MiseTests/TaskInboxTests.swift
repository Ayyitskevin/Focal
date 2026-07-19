import XCTest
@testable import Mise

final class TaskInboxTests: XCTestCase {
    func testSectioningUsesRequiredOrderAndDeterministicTies() {
        let tasks = [
            task(id: 1, dueOn: "2026-07-08"),
            task(id: 2, dueOn: "2026-07-08"),
            task(id: 3, dueOn: "2026-07-10"),
            task(id: 4, dueOn: "2026-07-12"),
            task(id: 5, dueOn: "2026-07-11"),
            task(id: 6, dueOn: nil),
            task(id: 7, dueOn: nil),
        ]

        let sections = TaskInboxSectioner.sections(
            tasks: tasks,
            today: LocalDate(rawValue: "2026-07-10")
        )

        XCTAssertEqual(
            sections.map(\.kind),
            [.overdue, .today, .upcoming, .noDueDate]
        )
        XCTAssertEqual(sections[0].tasks.map(\.id), [2, 1])
        XCTAssertEqual(sections[1].tasks.map(\.id), [3])
        XCTAssertEqual(sections[2].tasks.map(\.id), [5, 4])
        XCTAssertEqual(sections[3].tasks.map(\.id), [7, 6])
        XCTAssertTrue(sections[0].kind.isOverdue)
        XCTAssertFalse(sections[1].kind.isOverdue)
    }

    func testStudioTodayUsesWorkspaceZoneAndFallsBackToUTC() throws {
        let instant = try MiseJSON.decoder().decode(
            Date.self,
            from: Data(#""2026-07-10T03:30:00Z""#.utf8)
        )

        XCTAssertEqual(
            TaskInboxSectioner.studioToday(
                at: instant,
                timeZoneIdentifier: "America/New_York"
            ),
            LocalDate(rawValue: "2026-07-09")
        )
        XCTAssertEqual(
            TaskInboxSectioner.studioToday(
                at: instant,
                timeZoneIdentifier: "Asia/Tokyo"
            ),
            LocalDate(rawValue: "2026-07-10")
        )
        XCTAssertEqual(
            TaskInboxSectioner.studioToday(
                at: instant,
                timeZoneIdentifier: "Not/A-Time-Zone"
            ),
            LocalDate(rawValue: "2026-07-10")
        )
    }

    func testOverdueGroupingIgnoresStaleWireFlag() {
        let stale = task(id: 8, dueOn: "2026-07-09", isOverdue: false)

        let sections = TaskInboxSectioner.sections(
            tasks: [stale],
            today: LocalDate(rawValue: "2026-07-10")
        )

        XCTAssertEqual(sections.map(\.kind), [.overdue])
        XCTAssertTrue(sections[0].kind.isOverdue)
    }

    private func task(
        id: Int64,
        dueOn: String?,
        isOverdue: Bool = false
    ) -> TaskSummary {
        TaskSummary(
            id: id,
            title: "Studio task \(id)",
            dueOn: dueOn.map { LocalDate(rawValue: $0) },
            projectID: nil,
            projectTitle: nil,
            isOverdue: isOverdue
        )
    }
}
