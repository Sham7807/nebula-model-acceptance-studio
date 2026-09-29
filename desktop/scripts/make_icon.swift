import AppKit

// “Confluence”: two continuous channels crossing through an open diamond.
// The same high-resolution artwork supplies the sidebar and every Dock size.
let out = CommandLine.arguments[1]
let size = 1024
let image = NSImage(size:NSSize(width:size,height:size))
func color(_ r:CGFloat,_ g:CGFloat,_ b:CGFloat,_ alpha:CGFloat=1)->NSColor {
    NSColor(calibratedRed:r,green:g,blue:b,alpha:alpha)
}
func ribbon(_ points:[NSPoint],width:CGFloat)->NSBezierPath {
    let p=NSBezierPath();p.move(to:points[0]);points.dropFirst().forEach{p.line(to:$0)}
    p.lineWidth=width;p.lineJoinStyle = .round;p.lineCapStyle = .round
    return p
}
func shadedStroke(_ path:NSBezierPath,from:NSColor,to:NSColor,shadow:Bool=true){
    let cg=CGMutablePath()
    for i in 0..<path.elementCount {
        var points=[NSPoint](repeating:.zero,count:3)
        switch path.element(at:i,associatedPoints:&points){
        case .moveTo:cg.move(to:points[0])
        case .lineTo:cg.addLine(to:points[0])
        case .curveTo:cg.addCurve(to:points[2],control1:points[0],control2:points[1])
        case .closePath:cg.closeSubpath()
        default:break
        }
    }
    let stroke=cg.copy(strokingWithWidth:path.lineWidth,lineCap:.round,lineJoin:.round,miterLimit:10)
    let context=NSGraphicsContext.current!.cgContext
    context.saveGState()
    if shadow {
        context.setShadow(offset:CGSize(width:0,height:-9),blur:16,color:color(0.06,0.22,0.48,0.17).cgColor)
        context.addPath(stroke);context.setFillColor(from.cgColor);context.fillPath()
    }
    context.addPath(stroke);context.clip()
    let gradient=CGGradient(colorsSpace:CGColorSpaceCreateDeviceRGB(),colors:[from.cgColor,to.cgColor] as CFArray,locations:[0,1])!
    context.drawLinearGradient(gradient,start:CGPoint(x:245,y:780),end:CGPoint(x:795,y:260),options:[.drawsBeforeStartLocation,.drawsAfterEndLocation])
    context.restoreGState()
}
image.lockFocus()
NSGraphicsContext.current?.imageInterpolation = .high
let body=NSBezierPath(roundedRect:NSRect(x:72,y:72,width:880,height:880),xRadius:200,yRadius:200)
NSGraphicsContext.saveGraphicsState()
let tileShadow=NSShadow();tileShadow.shadowOffset=NSSize(width:0,height:-10);tileShadow.shadowBlurRadius=18;tileShadow.shadowColor=color(0.13,0.23,0.4,0.12);tileShadow.set()
color(0.98,0.99,1).setFill();body.fill()
NSGraphicsContext.restoreGraphicsState()
NSGradient(colors:[.white,color(0.945,0.965,0.985)])!.draw(in:body,angle:270)
color(0.84,0.89,0.94,0.75).setStroke();body.lineWidth=1.5;body.stroke()
// Two open chevrons overlap to create a compact woven junction. Broad rounded
// ends and generous negative space retain the silhouette at 16 and 32 pixels.
let teal=ribbon([NSPoint(x:580,y:744),NSPoint(x:328,y:492),NSPoint(x:574,y:246)],width:112)
shadedStroke(teal,from:color(0.18,0.83,0.85),to:color(0.02,0.59,0.75))
let blue=ribbon([NSPoint(x:446,y:744),NSPoint(x:698,y:492),NSPoint(x:452,y:246)],width:112)
shadedStroke(blue,from:color(0.15,0.49,1),to:color(0.19,0.28,0.81))
// Restore one short foreground segment at the lower crossing to weave the
// channels over-under, instead of flattening them into an ordinary link icon.
let lowerWeave=ribbon([NSPoint(x:450,y:370),NSPoint(x:552,y:268)],width:112)
shadedStroke(lowerWeave,from:color(0.18,0.83,0.85),to:color(0.02,0.59,0.75),shadow:false)
image.unlockFocus()
try FileManager.default.createDirectory(atPath:out,withIntermediateDirectories:true)
for s in [16,32,128,256,512] {
    for scale in [1,2] {
        let px=s*scale
        let rep=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:px,pixelsHigh:px,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
        NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:rep)
        NSGraphicsContext.current?.imageInterpolation = .high
        image.draw(in:NSRect(x:0,y:0,width:px,height:px),from:.zero,operation:.copy,fraction:1)
        NSGraphicsContext.restoreGraphicsState()
        try rep.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:out+"/icon_\(s)x\(s)\(scale == 2 ? "@2x" : "").png"))
    }
}
