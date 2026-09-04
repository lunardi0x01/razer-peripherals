import QtQuick
import qs.Ui
import "razer_api.js" as RazerApi

BarWidget {
  id: root
  moduleName: "lunardi0x01.razer-peripherals"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  readonly property var devices: panelLoader.item ? panelLoader.item.devices : []

  readonly property var keyboardDevice: RazerApi.findByKind(root.devices, "keyboard")
  readonly property var mouseDevice: RazerApi.findByKind(root.devices, "mouse")

  // "<kbd icon> <kbd %> | <mouse icon> <mouse %>" -- both slots always show
  // (icon plus a placeholder if that device has never been seen) so the
  // layout doesn't jump around as devices go to sleep and wake back up.
  // Uses a plain WidgetButton rather than BarIconButton: BarIconButton
  // forces a single fixed-width icon slot on a horizontal bar, which is
  // right for Hue/Ring's one-glyph icons but clips a wider two-device
  // readout.
  readonly property string displayText:
    RazerApi.deviceIcon("keyboard") + " " +
    RazerApi.formatPercent(root.keyboardDevice ? root.keyboardDevice.percent : null) +
    " | " +
    RazerApi.deviceIcon("mouse") + " " +
    RazerApi.formatPercent(root.mouseDevice ? root.mouseDevice.percent : null)

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.displayText
    tooltipText: "Razer peripherals"

    onPressed: function(b) {
      if (b === Qt.LeftButton) root.togglePanel()
    }
  }
}
