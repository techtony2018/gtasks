import EventKit
import Foundation

let args = CommandLine.arguments
let localFormatter = DateFormatter()
localFormatter.calendar = Calendar.current
localFormatter.timeZone = TimeZone.current
localFormatter.dateFormat = "yyyy-MM-dd"
guard args.count >= 4,
      let start = localFormatter.date(from: args[1]),
      let end = localFormatter.date(from: args[2]) else { exit(2) }
let store = EKEventStore()
var access = EKEventStore.authorizationStatus(for: .event)
if args[3] == "request" && access == .notDetermined {
    let semaphore = DispatchSemaphore(value: 0)
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { _, _ in semaphore.signal() }
    } else { store.requestAccess(to: .event) { _, _ in semaphore.signal() } }
    _ = semaphore.wait(timeout: .now() + 30)
    access = EKEventStore.authorizationStatus(for: .event)
}
let statusName: String
switch access {
case .notDetermined: statusName = "not_determined"
case .restricted: statusName = "restricted"
case .denied: statusName = "denied"
case .fullAccess, .authorized: statusName = "authorized"
case .writeOnly: statusName = "write_only"
@unknown default: statusName = "unavailable"
}
var response: [String: Any] = ["status": statusName, "events": []]
if access == .authorized || access == .fullAccess {
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
    response["events"] = store.events(matching: predicate).map { event in
        ["id": event.eventIdentifier ?? "", "title": event.title ?? "Untitled event", "day": localFormatter.string(from: event.startDate), "all_day": event.isAllDay]
    }
}
let data = try! JSONSerialization.data(withJSONObject: response)
print(String(data: data, encoding: .utf8)!)
