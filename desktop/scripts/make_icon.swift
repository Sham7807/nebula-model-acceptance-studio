import AppKit
let out = CommandLine.arguments[1]
let size = 1024
let image = NSImage(size:NSSize(width:size,height:size))
image.lockFocus()
let body = NSBezierPath(roundedRect:NSRect(x:80,y:80,width:864,height:864),xRadius:196,yRadius:196)
NSGradient(colors:[NSColor(calibratedRed:0.12,green:0.20,blue:0.40,alpha:1),NSColor(calibratedRed:0.10,green:0.12,blue:0.24,alpha:1)])!.draw(in:body,angle:270)
let orb = NSBezierPath(ovalIn:NSRect(x:270,y:270,width:484,height:484))
NSGradient(colors:[NSColor(calibratedRed:0.62,green:0.82,blue:1,alpha:1),NSColor(calibratedRed:0.19,green:0.47,blue:0.96,alpha:1),NSColor(calibratedRed:0.26,green:0.20,blue:0.70,alpha:1)])!.draw(in:orb,angle:315)
NSColor.white.withAlphaComponent(0.20).setStroke()
let ring=NSBezierPath(ovalIn:NSRect(x:198,y:198,width:628,height:628));ring.lineWidth=2;ring.stroke()
NSColor(calibratedRed:0.74,green:0.85,blue:1,alpha:1).setFill();NSBezierPath(ovalIn:NSRect(x:765,y:640,width:33,height:33)).fill()
NSColor.white.setFill()
let star=NSBezierPath();star.move(to:NSPoint(x:512,y:655));star.curve(to:NSPoint(x:648,y:512),controlPoint1:NSPoint(x:541,y:550),controlPoint2:NSPoint(x:551,y:541));star.curve(to:NSPoint(x:512,y:369),controlPoint1:NSPoint(x:551,y:483),controlPoint2:NSPoint(x:541,y:474));star.curve(to:NSPoint(x:376,y:512),controlPoint1:NSPoint(x:483,y:474),controlPoint2:NSPoint(x:473,y:483));star.curve(to:NSPoint(x:512,y:655),controlPoint1:NSPoint(x:473,y:541),controlPoint2:NSPoint(x:483,y:550));star.close();star.fill()
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
