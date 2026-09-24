import AVFoundation
import AppKit
let output = URL(fileURLWithPath:CommandLine.arguments[1])
try? FileManager.default.removeItem(at:output)
let writer=try AVAssetWriter(outputURL:output,fileType:.mp4)
let input=AVAssetWriterInput(mediaType:.video,outputSettings:[AVVideoCodecKey:AVVideoCodecType.h264,AVVideoWidthKey:320,AVVideoHeightKey:180])
let adapter=AVAssetWriterInputPixelBufferAdaptor(assetWriterInput:input,sourcePixelBufferAttributes:[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32ARGB,kCVPixelBufferWidthKey as String:320,kCVPixelBufferHeightKey as String:180])
writer.add(input);writer.startWriting();writer.startSession(atSourceTime:.zero)
for i in 0..<15 {
 while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval:0.01) }
 var pixel:CVPixelBuffer?;CVPixelBufferPoolCreatePixelBuffer(nil,adapter.pixelBufferPool!,&pixel)
 CVPixelBufferLockBaseAddress(pixel!,[])
 let ctx=CGContext(data:CVPixelBufferGetBaseAddress(pixel!),width:320,height:180,bitsPerComponent:8,bytesPerRow:CVPixelBufferGetBytesPerRow(pixel!),space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.noneSkipFirst.rawValue)!
 ctx.setFillColor(NSColor.systemBlue.cgColor);ctx.fill(CGRect(x:0,y:0,width:320,height:180));ctx.setFillColor(NSColor.white.cgColor);ctx.fillEllipse(in:CGRect(x:CGFloat(i*15+10),y:60,width:50,height:50))
 CVPixelBufferUnlockBaseAddress(pixel!,[]);adapter.append(pixel!,withPresentationTime:CMTime(value:Int64(i),timescale:15))
}
input.markAsFinished();let signal=DispatchSemaphore(value:0);writer.finishWriting { signal.signal() };signal.wait()
if writer.status != .completed { throw writer.error! }
print("Generated local H.264 fixture")
