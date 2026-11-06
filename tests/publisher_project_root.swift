// Run together with the resolver prefix from PublisherApp.swift, without launching UI.
let home = URL(fileURLWithPath: "/Users/test-user")
let cloud = home.appendingPathComponent("Library/Mobile Documents/com~apple~CloudDocs/台股上市櫃融資維持率")
let local = URL(fileURLWithPath: "/tmp/local-project")
let app = URL(fileURLWithPath: "/Applications/更新並發布.app")
let required = ["scripts/launch_publisher.sh", "scripts/update_and_publish.py", "AGENTS.md", "計算方式.md"]
func paths(_ root: URL) -> Set<String> { Set(required.map { root.appendingPathComponent($0).path }) }
let cloudFiles = paths(cloud)
assert(publisherProjectRoot(bundle: app, home: home, readable: cloudFiles.contains)?.path == cloud.path)
let both = paths(local).union(cloudFiles)
assert(publisherProjectRoot(bundle: local.appendingPathComponent("更新並發布.app"), home: home, readable: both.contains)?.path == local.path)
assert(publisherProjectRoot(bundle: app, home: home, readable: { _ in false }) == nil)
let partial = Set([cloud.appendingPathComponent("scripts/launch_publisher.sh").path])
assert(publisherProjectRoot(bundle: app, home: home, readable: partial.contains) == nil)
assert(publisherProjectRoot(bundle: local.appendingPathComponent("更新並發布.app"), home: home, readable: cloudFiles.contains)?.path == cloud.path)
print("5 project resolution checks passed")
