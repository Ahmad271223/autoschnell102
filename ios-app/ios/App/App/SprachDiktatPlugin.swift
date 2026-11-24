// Sprachdiktat auf dem iPhone (Wunsch Ahmad 24.11.2026).
//
// Die Webseite (app.auto-schnellkauf.de, live in der Capacitor-Huelle) ruft
// dieses Plugin ueber window.Capacitor.nativePromise("SprachDiktat", ...) auf.
// Spracherkennung mit WhisperKit (argmax-oss-swift, MIT-Lizenz) direkt auf dem
// Geraet: der Ton verlaesst das iPhone NIE, es gibt keinen Server-Aufruf und
// keine laufenden Kosten. Nur das Sprachmodell wird einmalig von Hugging Face
// (argmaxinc/whisperkit-coreml) geladen und danach offline genutzt.
//
// Methoden (alle Promises):
//   status()          -> { phase, fortschritt, modell, groesseMB, fehler, aufnahme }
//   vorbereiten()     -> laedt + kompiliert das Modell (einmalig, Fortschritt per status())
//   aufnahmeStarten() -> fragt einmalig nach dem Mikrofon, nimmt auf (max. 5 Minuten)
//   aufnahmeStoppen() -> { text }  (deutsch)
//   abbrechen()       -> Aufnahme verwerfen
//
// phase: "fehlt" (Modell noch nicht geladen), "laedt", "bereit", "fehler".

import Foundation
import AVFoundation
import Capacitor
import WhisperKit

@objc(SprachDiktatPlugin)
public class SprachDiktatPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SprachDiktatPlugin"
    public let jsName = "SprachDiktat"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "vorbereiten", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "aufnahmeStarten", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "aufnahmeStoppen", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "abbrechen", returnType: CAPPluginReturnPromise),
    ]

    private let motor = DiktatMotor()

    @objc func status(_ call: CAPPluginCall) {
        Task {
            call.resolve(await motor.status())
        }
    }

    @objc func vorbereiten(_ call: CAPPluginCall) {
        Task {
            // Antwortet sofort; das Laden laeuft weiter, die Seite fragt status() ab.
            await motor.vorbereitenImHintergrund()
            call.resolve(await motor.status())
        }
    }

    @objc func aufnahmeStarten(_ call: CAPPluginCall) {
        Task {
            do {
                try await motor.aufnahmeStarten()
                call.resolve(["aufnahme": true])
            } catch {
                call.reject(DiktatMotor.meldung(error), DiktatMotor.code(error))
            }
        }
    }

    @objc func aufnahmeStoppen(_ call: CAPPluginCall) {
        Task {
            do {
                let text = try await motor.aufnahmeStoppenUndErkennen()
                call.resolve(["text": text])
            } catch {
                call.reject(DiktatMotor.meldung(error), DiktatMotor.code(error))
            }
        }
    }

    @objc func abbrechen(_ call: CAPPluginCall) {
        Task {
            await motor.abbrechen()
            call.resolve()
        }
    }
}

enum DiktatFehler: Error {
    case keinMikrofon
    case modellNichtBereit
    case keineAufnahme
    case aufnahmeLaeuft
    case aufnahmeFehlgeschlagen
}

actor DiktatMotor {
    /// Beste Qualitaet fuer Deutsch zuerst; genommen wird das erste, das
    /// WhisperKit fuer dieses iPhone als unterstuetzt meldet.
    private static let bevorzugt: [(name: String, mb: Int)] = [
        ("openai_whisper-large-v3-v20240930_turbo_632MB", 632),
        ("openai_whisper-small", 490),
        ("openai_whisper-base", 150),
    ]
    private static let maxSekunden: TimeInterval = 300
    private static let ordnerSchluessel = "sprachdiktat.modellordner"
    private static let modellSchluessel = "sprachdiktat.modell"

    private var pipe: WhisperKit?
    private var phase = "fehlt"
    private var fortschritt: Double = 0
    private var fehler: String?
    private var ladeTask: Task<Void, Never>?
    private var recorder: AVAudioRecorder?
    private var aufnahmeUrl: URL?

    private let gewaehlt: (name: String, mb: Int)

    init() {
        let unterstuetzt = Set(WhisperKit.recommendedModels().supported)
        gewaehlt = Self.bevorzugt.first { unterstuetzt.contains($0.name) } ?? Self.bevorzugt[2]
    }

    func status() -> [String: Any] {
        return [
            "phase": phase,
            "fortschritt": fortschritt,
            "modell": gewaehlt.name,
            "groesseMB": gewaehlt.mb,
            "heruntergeladen": gespeicherterOrdner() != nil,
            "fehler": fehler ?? "",
            "aufnahme": recorder != nil,
        ]
    }

    func vorbereitenImHintergrund() {
        if pipe != nil || ladeTask != nil { return }
        phase = "laedt"
        fortschritt = 0
        fehler = nil
        ladeTask = Task { await self.laden() }
    }

    private func laden() async {
        do {
            let ordner: URL
            if let vorhanden = gespeicherterOrdner() {
                ordner = vorhanden
                fortschritt = 0.9
            } else {
                let basis = try Self.speicherOrt()
                ordner = try await WhisperKit.download(
                    variant: gewaehlt.name,
                    downloadBase: basis,
                    progressCallback: { p in
                        let wert = p.fractionCompleted * 0.9
                        Task { await self.setzeFortschritt(wert) }
                    }
                )
                Self.vomBackupAusnehmen(basis)
                UserDefaults.standard.set(ordner.path, forKey: Self.ordnerSchluessel)
                UserDefaults.standard.set(gewaehlt.name, forKey: Self.modellSchluessel)
            }
            let config = WhisperKitConfig(
                model: gewaehlt.name,
                modelFolder: ordner.path,
                verbose: false,
                prewarm: true,
                load: true,
                download: false
            )
            pipe = try await WhisperKit(config)
            fortschritt = 1
            phase = "bereit"
        } catch {
            // Halb geladenes Modell nicht als vorhanden merken.
            UserDefaults.standard.removeObject(forKey: Self.ordnerSchluessel)
            phase = "fehler"
            fehler = "Sprachmodell konnte nicht geladen werden. Bitte mit WLAN erneut versuchen."
        }
        ladeTask = nil
    }

    private func setzeFortschritt(_ wert: Double) {
        if phase == "laedt" { fortschritt = max(fortschritt, wert) }
    }

    func aufnahmeStarten() async throws {
        guard pipe != nil else { throw DiktatFehler.modellNichtBereit }
        guard recorder == nil else { throw DiktatFehler.aufnahmeLaeuft }
        guard await Self.mikrofonErlaubt() else { throw DiktatFehler.keinMikrofon }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .default)
        try session.setActive(true)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("diktat-\(UUID().uuidString).wav")
        let einstellungen: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let r = try AVAudioRecorder(url: url, settings: einstellungen)
        guard r.record(forDuration: Self.maxSekunden) else {
            try? session.setActive(false, options: .notifyOthersOnDeactivation)
            throw DiktatFehler.aufnahmeFehlgeschlagen
        }
        recorder = r
        aufnahmeUrl = url
    }

    func aufnahmeStoppenUndErkennen() async throws -> String {
        guard let r = recorder, let url = aufnahmeUrl else { throw DiktatFehler.keineAufnahme }
        r.stop()
        recorder = nil
        aufnahmeUrl = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        defer { try? FileManager.default.removeItem(at: url) }

        guard let pipe = pipe else { throw DiktatFehler.modellNichtBereit }
        let optionen = DecodingOptions(
            task: .transcribe,
            language: "de",
            temperature: 0,
            usePrefillPrompt: true,
            detectLanguage: false,
            skipSpecialTokens: true,
            withoutTimestamps: true,
            chunkingStrategy: .vad
        )
        let ergebnisse = try await pipe.transcribe(audioPath: url.path, decodeOptions: optionen)
        return ergebnisse
            .map { $0.text.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    func abbrechen() {
        recorder?.stop()
        recorder = nil
        if let url = aufnahmeUrl { try? FileManager.default.removeItem(at: url) }
        aufnahmeUrl = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    // MARK: - Hilfen

    private func gespeicherterOrdner() -> URL? {
        guard UserDefaults.standard.string(forKey: Self.modellSchluessel) == gewaehlt.name,
              let pfad = UserDefaults.standard.string(forKey: Self.ordnerSchluessel),
              FileManager.default.fileExists(atPath: pfad) else { return nil }
        return URL(fileURLWithPath: pfad)
    }

    private static func speicherOrt() throws -> URL {
        let basis = try FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true
        ).appendingPathComponent("Sprachmodell", isDirectory: true)
        try FileManager.default.createDirectory(at: basis, withIntermediateDirectories: true)
        return basis
    }

    /// Das Modell (bis 632 MB) gehoert nicht ins iCloud-Backup der Nutzer.
    private static func vomBackupAusnehmen(_ url: URL) {
        var u = url
        var werte = URLResourceValues()
        werte.isExcludedFromBackup = true
        try? u.setResourceValues(werte)
    }

    private static func mikrofonErlaubt() async -> Bool {
        if #available(iOS 17.0, *) {
            switch AVAudioApplication.shared.recordPermission {
            case .granted: return true
            case .denied: return false
            default: return await AVAudioApplication.requestRecordPermission()
            }
        } else {
            let session = AVAudioSession.sharedInstance()
            switch session.recordPermission {
            case .granted: return true
            case .denied: return false
            default:
                return await withCheckedContinuation { fortsetzung in
                    session.requestRecordPermission { fortsetzung.resume(returning: $0) }
                }
            }
        }
    }

    nonisolated static func code(_ error: Error) -> String {
        switch error as? DiktatFehler {
        case .keinMikrofon: return "KEIN_MIKROFON"
        case .modellNichtBereit: return "MODELL_FEHLT"
        case .keineAufnahme: return "KEINE_AUFNAHME"
        case .aufnahmeLaeuft: return "AUFNAHME_LAEUFT"
        default: return "FEHLER"
        }
    }

    nonisolated static func meldung(_ error: Error) -> String {
        switch error as? DiktatFehler {
        case .keinMikrofon:
            return "Kein Zugriff aufs Mikrofon. Bitte in den iPhone-Einstellungen unter AutoSchnell erlauben."
        case .modellNichtBereit:
            return "Das Sprachmodell ist noch nicht geladen."
        case .keineAufnahme:
            return "Es laeuft keine Aufnahme."
        case .aufnahmeLaeuft:
            return "Es laeuft schon eine Aufnahme."
        default:
            return "Diktat fehlgeschlagen. Bitte erneut versuchen."
        }
    }
}
