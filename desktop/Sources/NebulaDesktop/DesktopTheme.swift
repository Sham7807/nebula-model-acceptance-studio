import SwiftUI

enum DesktopTheme {
    static let canvas = Color.white
    static let sidebar = Color(red:0.985,green:0.987,blue:0.992)
    static let surface = Color(red:0.975,green:0.980,blue:0.987)
    static let line = Color(red:0.90,green:0.92,blue:0.94)
    static let accent = Color(red:0.04,green:0.44,blue:0.92)
}

struct DesktopCardButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        Card(configuration:configuration,reduceMotion:reduceMotion)
    }
    private struct Card: View {
        let configuration: Configuration
        let reduceMotion: Bool
        @State private var hovered = false
        var body: some View {
            configuration.label
                .background(hovered ? DesktopTheme.surface : .white,in:RoundedRectangle(cornerRadius:20,style:.continuous))
                .overlay(RoundedRectangle(cornerRadius:20,style:.continuous).stroke(hovered ? DesktopTheme.accent.opacity(0.24) : DesktopTheme.line,lineWidth:1))
                .shadow(color:.black.opacity(hovered ? 0.045 : 0.015),radius:hovered ? 12 : 5,y:3)
                .scaleEffect(configuration.isPressed && !reduceMotion ? 0.988 : 1)
                .animation(reduceMotion ? nil : .easeOut(duration:0.16),value:hovered)
                .animation(reduceMotion ? nil : .easeOut(duration:0.12),value:configuration.isPressed)
                .onHover { hovered=$0 }
        }
    }
}
