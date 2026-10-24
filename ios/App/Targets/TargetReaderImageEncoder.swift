import CoreImage
import ImageIO
import UIKit

struct TargetReaderSnapshot {
    let jpeg: Data
    let image: UIImage
}

enum TargetReaderImageEncoder {
    static let maximumBytes = 512 * 1_024
    private static let context = CIContext(options: [.cacheIntermediates: false])

    static func encode(_ frame: NativeMediaVideoFrame) throws -> TargetReaderSnapshot {
        let orientation: CGImagePropertyOrientation
        switch frame.rotation {
        case .degrees0: orientation = .up
        case .degrees90: orientation = .right
        case .degrees180: orientation = .down
        case .degrees270: orientation = .left
        }
        let upright = CIImage(cvPixelBuffer: frame.pixelBuffer).oriented(orientation)
        let longest = max(upright.extent.width, upright.extent.height)
        guard longest > 0 else { throw EncodingError.invalidImage }
        var scale = min(1, 1_600 / longest)
        for _ in 0..<5 {
            let resized = upright.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            guard let rendered = context.createCGImage(resized, from: resized.extent) else {
                throw EncodingError.invalidImage
            }
            let image = UIImage(cgImage: rendered, scale: 1, orientation: .up)
            for quality in [0.85, 0.65, 0.45] {
                if let jpeg = image.jpegData(compressionQuality: quality), jpeg.count <= maximumBytes {
                    return TargetReaderSnapshot(jpeg: jpeg, image: image)
                }
            }
            scale *= 0.7
        }
        throw EncodingError.imageTooLarge
    }

    enum EncodingError: Error { case invalidImage, imageTooLarge }
}
