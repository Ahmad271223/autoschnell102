import UIKit
import Capacitor

/// Eigener Bridge-Controller, damit das App-eigene Plugin (SprachDiktat)
/// registriert wird — Capacitor findet nur npm-Plugins automatisch.
/// In Main.storyboard als customClass eingetragen.
class MainViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(SprachDiktatPlugin())
    }
}
