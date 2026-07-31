import EventKit
import Foundation

// This executable intentionally has no EKEventStore save/delete/create calls.
// It is bundled as “Mission Control Calendar.app” so macOS TCC attributes the
// Full Access request to Mission Control rather than the Python web process.
let arguments = CommandLine.arguments
guard arguments.count >= 2 else { exit(2) }

let action = arguments[1]
let store = EKEventStore()
let outputIndex = arguments.firstIndex(of: "--output")
let outputPath = outputIndex.flatMap { index in
    index + 1 < arguments.count ? arguments[index + 1] : nil
}

func statusName() -> String {
    let access = EKEventStore.authorizationStatus(for: .event)
    switch access {
    case .notDetermined: return "not_determined"
    case .restricted: return "restricted"
    case .denied: return "denied"
    case .fullAccess, .authorized: return "authorized"
    case .writeOnly: return "write_only"
    @unknown default: return "unavailable"
    }
}

func printJSON(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value),
          let text = String(data: data, encoding: .utf8) else { exit(3) }
    if let outputPath {
        try? text.write(toFile: outputPath, atomically: true, encoding: .utf8)
    } else {
        print(text)
    }
}

func requestFullAccess() {
    guard statusName() == "not_determined" else {
        printJSON(["status": statusName()])
        return
    }
    let semaphore = DispatchSemaphore(value: 0)
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { _, _ in semaphore.signal() }
    } else {
        store.requestAccess(to: .event) { _, _ in semaphore.signal() }
    }
    _ = semaphore.wait(timeout: .now() + 60)
    printJSON(["status": statusName()])
}

switch action {
case "status":
    printJSON(["status": statusName()])
case "request_full_access":
    requestFullAccess()
case "calendars":
    guard statusName() == "authorized" else {
        printJSON(["status": statusName(), "calendars": []])
        exit(0)
    }
    let calendars = store.calendars(for: .event).map { calendar in
        ["id": calendar.calendarIdentifier, "title": calendar.title, "color": calendar.cgColor?.components.map(String.init(describing:)) ?? []] as [String: Any]
    }
    printJSON(["status": statusName(), "calendars": calendars])
case "events":
    guard arguments.count == 5,
          let start = ISO8601DateFormatter().date(from: arguments[2] + "T00:00:00Z"),
          let end = ISO8601DateFormatter().date(from: arguments[3] + "T00:00:00Z"),
          let rawIDs = arguments[4].data(using: .utf8),
          let selectedIDs = try? JSONSerialization.jsonObject(with: rawIDs) as? [String] else { exit(2) }
    guard statusName() == "authorized" else {
        printJSON(["status": statusName(), "events": []])
        exit(0)
    }
    let selected = store.calendars(for: .event).filter { selectedIDs.contains($0.calendarIdentifier) }
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: selected)
    let formatter = DateFormatter()
    formatter.calendar = Calendar.current
    formatter.timeZone = TimeZone.current
    formatter.dateFormat = "yyyy-MM-dd"
    let events = store.events(matching: predicate).map { event in
        ["id": event.eventIdentifier ?? "", "title": event.title ?? "Untitled event", "day": formatter.string(from: event.startDate), "all_day": event.isAllDay] as [String: Any]
    }
    printJSON(["status": statusName(), "events": events])
default:
    exit(2)
}
