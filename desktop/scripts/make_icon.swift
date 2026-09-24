import AppKit
let out = CommandLine.arguments[1]
let size = 1024
let image = NSImage(size:NSSize(width:size,height:size))
image.lockFocus()
let body = NSBezierPath(roundedRect:NSRect(x:80,y:80,width:864,height:864),xRadius:196,yRadius:196)
NSGradient(colors:[.white,NSColor(calibratedRed:0.93,green:0.96,blue:1,alpha:1)])!.draw(in:body,angle:270)
NSColor(calibratedRed:0.85,green:0.89,blue:0.95,alpha:1).setStroke();body.lineWidth=2;body.stroke()
let blue=NSColor(calibratedRed:0.03,green:0.45,blue:0.94,alpha:1)
let connectors=NSBezierPath();connectors.move(to:NSPoint(x:332,y:635));connectors.line(to:NSPoint(x:692,y:635));connectors.line(to:NSPoint(x:512,y:326));connectors.close()
blue.withAlphaComponent(0.20).setStroke();connectors.lineWidth=26;connectors.lineJoinStyle = .round;connectors.stroke()
for (x,y) in [(332.0,635.0),(692.0,635.0),(512.0,326.0)] {
 let node=NSBezierPath(roundedRect:NSRect(x:x-84,y:y-84,width:168,height:168),xRadius:48,yRadius:48)
 NSGradient(colors:[blue,NSColor(calibratedRed:0.16,green:0.58,blue:1,alpha:1)])!.draw(in:node,angle:90)
 NSColor.white.setFill();NSBezierPath(ovalIn:NSRect(x:x-18,y:y-18,width:36,height:36)).fill()
}
image.unlockFocus()
try FileManager.default.createDirectory(atPath:out,withIntermediateDirectories:true)
for s in [16,32,128,256,512] {
 for scale in [1,2] {
  let px=s*scale
  let rep=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:px,pixelsHigh:px,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
  NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:rep)
  image.draw(in:NSRect(x:0,y:0,width:px,height:px),from:.zero,operation:.copy,fraction:1)
  NSGraphicsContext.restoreGraphicsState()
  try rep.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:out+"/icon_\(s)x\(s)\(scale == 2 ? "@2x" : "").png"))
 }
}
