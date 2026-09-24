import Darwin
var assertionCount = 0
func XCTAssertTrue(_ value:Bool,_ note:String="",file:StaticString=#file,line:UInt=#line) { assertionCount += 1; if !value { fatalError("Assertion failed: \(note)",file:file,line:line) } }
func XCTAssertFalse(_ value:Bool,_ note:String="",file:StaticString=#file,line:UInt=#line) { XCTAssertTrue(!value,note,file:file,line:line) }
func XCTAssertEqual<T:Equatable>(_ lhs:T,_ rhs:T,file:StaticString=#file,line:UInt=#line) { XCTAssertTrue(lhs == rhs,"Values did not match",file:file,line:line) }
func XCTAssertNil<T>(_ value:T?,file:StaticString=#file,line:UInt=#line) { XCTAssertTrue(value == nil,"Expected nil",file:file,line:line) }
func XCTUnwrap<T>(_ value:T?) throws -> T { guard let value else { throw NSError(domain:"UnexpectedNil",code:1) }; return value }
func XCTFail(_ message:String) { print("FAIL: \(message)") }
import Foundation
import WebKit
import AppKit


final class ValueTests {
    func testChannelURLs() {
        for base in ["https://api.example.com/v1","http://127.0.0.1:8877","https://测试.example/v1"] { XCTAssertTrue(ChannelProfile.validate(base:base),base) }
        for base in ["file:///tmp/x","javascript:alert(1)","https://user:secret@example.com","https://example.com?key=secret","https://example.com/#key","https://bad host.com",""] { XCTAssertFalse(ChannelProfile.validate(base:base),base) }
    }
    func testOriginIncludesSchemeAndPort() throws {
        let session=try JSONDecoder().decode(EngineSession.self,from:Data(#"{"type":"ready","url":"http://127.0.0.1:8811","cookieName":"test","cookie":"test","csrf":"test","pid":1}"#.utf8))
        XCTAssertTrue(session.contains(URL(string:"http://127.0.0.1:8811/api/history")!))
        for url in ["https://127.0.0.1:8811/","http://127.0.0.1:8812/","http://example.com:8811/"] { XCTAssertFalse(session.contains(URL(string:url)!)) }
    }
    func testProfileNeverSerializesAPIKey() throws {
        let data=try JSONEncoder().encode(ChannelProfile())
        let object=try XCTUnwrap(JSONSerialization.jsonObject(with:data) as? [String:Any])
        XCTAssertEqual(Set(object.keys),Set(["name","base","model","rememberKey"]))
    }
}

@MainActor
final class WorkspaceTests {
    func eventually(_ label:String,seconds:Double=15,_ predicate:() async throws -> Bool) async throws {
        let deadline=Date().addingTimeInterval(seconds)
        while Date()<deadline { if try await predicate() { assertionCount += 1; print("PASS: \(label)"); return };try await Task.sleep(for:.milliseconds(100)) }
        XCTFail("Timeout: \(label)");throw NSError(domain:"TestTimeout",code:1)
    }
    func js(_ web:WKWebView,_ source:String) async throws -> Any { try await web.evaluateJavaScript(source) ?? NSNull() }
    func testBundledEngineAndAllWorkspaces() async throws {
        guard let path=ProcessInfo.processInfo.environment["NEBULA_TEST_RESOURCES"] else { throw NSError(domain:"MissingResources",code:1) }
        let resources=URL(fileURLWithPath:path),data=FileManager.default.temporaryDirectory.appendingPathComponent("Nebula-WebKit-QA-"+UUID().uuidString)
        let engine=EngineService(resources:resources,dataDirectory:data),workspace=WebWorkspace(resources:resources)
        _ = NSApplication.shared
        let window=NSWindow(contentRect:NSRect(x:0,y:0,width:1080,height:780),styleMask:[.titled],backing:.buffered,defer:false)
        window.contentView=workspace.webView;window.orderFront(nil)
        defer { window.orderOut(nil);try? FileManager.default.removeItem(at:data) }
        engine.start()
        do {
            try await eventually("engine ready") { engine.isReady }
            await workspace.connect(try XCTUnwrap(engine.session))
            try await eventually("WebKit bridge ready") { workspace.loaded }
            let historyData=try await engine.request("/api/history?limit=5&offset=0")
            let page=try JSONDecoder().decode(HistoryPage.self,from:historyData)
            XCTAssertEqual(page.stats.total,0)
            let checks:[(String,String)]=[
                ("image","!document.getElementById('basicView').hidden && document.querySelector('.kind-tab[data-kind=image]').classList.contains('active')"),
                ("video","document.querySelector('.kind-tab[data-kind=video]').classList.contains('active')"),
                ("audio","document.querySelector('.kind-tab[data-kind=audio]').classList.contains('active')"),
                ("general","!document.getElementById('legacyFrame').hidden && !!document.getElementById('legacyFrame').contentDocument?.getElementById('inBase')"),
                ("ccmax","document.querySelector('[data-suite=ccmax]').classList.contains('active') && !document.getElementById('acceptancePanel').hidden"),
                ("claude","document.querySelector('[data-suite=claude]').classList.contains('active')"),
                ("kimi","document.querySelector('[data-suite=kimi]').classList.contains('active')"),
                ("gpt","!document.getElementById('gptPanel').hidden"),
                ("history","!document.getElementById('historyView').hidden"),
                ("text","!document.getElementById('basicView').hidden && document.querySelector('.kind-tab[data-kind=text]').classList.contains('active')")]
            for (route,check) in checks {
                workspace.navigate(route)
                try await eventually(route) { try await self.js(workspace.webView,check) as? Bool == true }
                XCTAssertNil(workspace.error)
            }
            var profile=ChannelProfile();profile.base="http://127.0.0.1:18991";profile.model="fixture-model"
            workspace.configure(profile,key:"qa-placeholder")
            try await eventually("shared channel configuration") {
                try await self.js(workspace.webView,"document.getElementById('acceptanceModel').value === 'fixture-model' && document.getElementById('legacyFrame').contentDocument.getElementById('inModel').value === 'fixture-model'") as? Bool == true
            }
            if ProcessInfo.processInfo.environment["NEBULA_TEST_PROVIDER"] == "1" {
                for (route,button,check) in [
                    ("text","document.getElementById('loadModels').click()","document.getElementById('modelList').textContent.includes('fixture-second')"),
                    ("general","document.getElementById('legacyFrame').contentDocument.getElementById('loadGeneralModels').click()","document.getElementById('legacyFrame').contentDocument.getElementById('loadGeneralModels').textContent === '刷新模型'"),
                    ("claude","document.getElementById('acceptanceModels').click()","document.body.textContent.includes('fixture-second')"),
                    ("gpt","document.getElementById('gptModels').click()","document.body.textContent.includes('fixture-second')")
                ] {
                    workspace.navigate(route);try await Task.sleep(for:.milliseconds(200))
                    _ = try await js(workspace.webView,button)
                    do { try await eventually("model discovery "+route) { try await self.js(workspace.webView,check) as? Bool == true } }
                    catch { print("Discovery diagnostic:", try await js(workspace.webView,"document.getElementById('legacyFrame').contentDocument.getElementById('generalModelHint').textContent")); throw error }
                }
                workspace.navigate("text");try await Task.sleep(for:.milliseconds(200))
                _ = try await js(workspace.webView,"document.getElementById('runBtn').click()")
                try await eventually("text result") { try await self.js(workspace.webView,"document.getElementById('results').textContent.includes('DESKTOP_FIXTURE_OK')") as? Bool == true }
                try await eventually("durable history") {
                    let response=try await engine.request("/api/history")
                    return try JSONDecoder().decode(HistoryPage.self,from:response).stats.total > 0
                }
            }
            if ProcessInfo.processInfo.environment["NEBULA_TEST_PROVIDER"] == "1" {
                for route in ["image","audio","video"] {
                    workspace.navigate(route);try await Task.sleep(for:.milliseconds(250))
                    _ = try await js(workspace.webView,"document.getElementById('clearBtn').click();document.getElementById('model').value='fixture-'+ '\(route)';document.getElementById('model').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('allowMismatch').checked=true;document.getElementById('runBtn').click()")
                    let selector=route == "image" ? "img" : route
                    try await eventually("media preview "+route) {
                        try await self.js(workspace.webView,"(()=>{const el=document.querySelector('#results \(selector)');return !!el && \(route == "image" ? "el.complete && el.naturalWidth>0" : "el.readyState>=1");})()") as? Bool == true
                    }
                }
            }
            await engine.stop();XCTAssertEqual(engine.state,.idle)
        } catch { await engine.stop();throw error }
    }
}

@main
struct NativeChecks {
    @MainActor static func main() {
        _ = NSApplication.shared
        Task { @MainActor in
            do {
                let values=ValueTests(); values.testChannelURLs();try values.testOriginIncludesSchemeAndPort();try values.testProfileNeverSerializesAPIKey()
                try await WorkspaceTests().testBundledEngineAndAllWorkspaces()
                print("PASS: Native value, engine and WebKit checks (\(assertionCount) assertions)")
                exit(0)
            } catch { print("FAIL: \(error)");exit(1) }
        }
        NSApplication.shared.run()
    }
}
