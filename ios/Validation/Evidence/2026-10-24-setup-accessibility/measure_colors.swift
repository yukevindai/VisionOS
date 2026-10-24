import AppKit
let path = CommandLine.arguments[1]
let bitmap = NSBitmapImageRep(data: try Data(contentsOf: URL(fileURLWithPath: path)))!
print("colorSpace \(bitmap.colorSpace.localizedName ?? "unknown"), samples \(bitmap.samplesPerPixel), row \(bitmap.bytesPerRow), planar \(bitmap.isPlanar)")
var counts: [UInt32: Int] = [:]
let data = bitmap.bitmapData!
for y in stride(from: 0, to: bitmap.pixelsHigh, by: 3) {
    for x in stride(from: 0, to: bitmap.pixelsWide, by: 3) {
        let i = y * bitmap.bytesPerRow + x * (bitmap.bitsPerPixel / 8)
        let key = UInt32(data[i]) << 16 | UInt32(data[i+1]) << 8 | UInt32(data[i+2])
        counts[key, default: 0] += 1
    }
}
for (rgb, count) in counts.sorted(by: { $0.value > $1.value }).prefix(12) { print(String(format: "%06X", rgb), count) }
