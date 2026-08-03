import EventKit
import Foundation

// This executable intentionally has no EKEventStore save/delete/create calls.
// It is bundled as “Mission Control Calendar.app” so macOS TCC attributes the
// Full Access request to Mission Control rather than the Python web process.
let arguments = CommandLine.arguments
var positionalArguments = Array(arguments.dropFirst())
let outputIndex = positionalArguments.firstIndex(of: "--output")
let outputPath = outputIndex.flatMap { index in
    index + 1 < positionalArguments.count ? positionalArguments[index + 1] : nil
}
if let index = outputIndex {
    guard index + 1 < positionalArguments.count else { exit(2) }
    positionalArguments.removeSubrange(index ... index + 1)
}
guard let action = positionalArguments.first else { exit(2) }
let store = EKEventStore()

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

func availabilityName(_ availability: EKEventAvailability) -> String {
    switch availability {
    case .busy: return "Busy"
    case .free: return "Free"
    case .tentative: return "Tentative"
    case .unavailable: return "Unavailable"
    case .notSupported: return ""
    @unknown default: return ""
    }
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
    guard positionalArguments.count == 4,
          let start = ISO8601DateFormatter().date(from: positionalArguments[1] + "T00:00:00Z"),
          let end = ISO8601DateFormatter().date(from: positionalArguments[2] + "T00:00:00Z"),
          let rawIDs = positionalArguments[3].data(using: .utf8),
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
    let instantFormatter = ISO8601DateFormatter()
    let events = store.events(matching: predicate).compactMap { event -> [String: Any]? in
        guard let effectiveStart = event.startDate else { return nil }
        let effectiveEnd = event.endDate ?? effectiveStart
        let inclusiveEnd = event.isAllDay
            ? effectiveEnd.addingTimeInterval(-1)
            : effectiveEnd
        return [
            "id": event.eventIdentifier ?? "",
            "title": event.title ?? "Untitled event",
            "day": formatter.string(from: effectiveStart),
            "start_day": formatter.string(from: effectiveStart),
            "end_day": formatter.string(from: inclusiveEnd),
            "start": instantFormatter.string(from: effectiveStart),
            "end": instantFormatter.string(from: effectiveEnd),
            "all_day": event.isAllDay,
            "calendar_title": event.calendar.title,
            "location": event.location ?? "",
            "notes": event.notes ?? "",
            "url": event.url?.absoluteString ?? "",
            "recurrence": event.hasRecurrenceRules ? "Recurring" : "",
            "availability": availabilityName(event.availability),
            "timezone": event.timeZone?.identifier ?? TimeZone.current.identifier,
        ] as [String: Any]
    }
    printJSON(["status": statusName(), "events": events])
default:
    exit(2)
}
