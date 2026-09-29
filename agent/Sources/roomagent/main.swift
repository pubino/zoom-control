#if os(macOS)
RoomAgentCommand.main()
#else
import Foundation
import RoomAgentCore

let report = Report(command: CommandLine.arguments.dropFirst().first ?? "none", checks: [],
                    error: "roomagent requires macOS")
print(report.json())
exit(3)
#endif
