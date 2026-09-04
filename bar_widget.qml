import QtQuick
import qs.Commons
import qs.Ui
import "razer_api.js" as RazerApi

BarWidget {
  id: root
  moduleName: "lunardi0x01.razer-peripherals"

  readonly property int lowBatteryThreshold: 25

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

  // KeyboardPanel.close() checks `"close" in owner` and, finding neither
  // open() nor close() here, falls back to imperatively assigning its own
  // internal `open` property directly -- which permanently breaks the
  // one-way `open: root.opened` binding declared in panel.qml's
  // KeyboardPanel instance (any imperative assignment to a bound property
  // detaches the binding in QML). Once that binding is broken, this
  // widget's own open()/close()/toggle() keep updating root.opened
  // correctly, but it never reaches the visible window again until the
  // shell restarts -- exactly the "closes once via outside-click, then
  // never reopens" bug this fixes. Defining open()/close() here (mirroring
  // Hue's bar_widget.qml) makes KeyboardPanel route every close through
  // Panel.close() -> panelController.hide() instead, so the binding is
  // never touched imperatively from outside panel.qml.
  function open() {
    if (panelLoader.item && panelLoader.item.open) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  readonly property var devices: panelLoader.item ? panelLoader.item.devices : []

  readonly property var keyboardDevice: RazerApi.findByKind(root.devices, "keyboard")
  readonly property var mouseDevice: RazerApi.findByKind(root.devices, "mouse")

  function iconColorFor(device) {
    if (device && typeof device.percent === "number" && device.percent < root.lowBatteryThreshold) {
      return root.bar ? root.bar.urgent : Color.urgent
    }
    return root.bar ? root.bar.barForeground : Color.foreground
  }

  readonly property string tooltipText:
    "Keyboard " + RazerApi.formatPercent(root.keyboardDevice ? root.keyboardDevice.percent : null) +
    "  |  Mouse " + RazerApi.formatPercent(root.mouseDevice ? root.mouseDevice.percent : null)

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

  // Plain WidgetButton rather than BarIconButton: BarIconButton's `text`
  // renders through a single Text element with one color, which can't show
  // the keyboard and mouse glyphs in two independent colors (each needs to
  // turn red on its own low-battery threshold, independent of the other).
  // labelVisible is off since WidgetButton's own single-Text label is
  // unused -- iconRow below is the real content, sized explicitly via
  // fixedWidth/fixedHeight since an invisible empty label has no size to
  // drive the button's own auto-sizing.
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    fixedWidth: iconRow.implicitWidth + scaledHorizontalMargin * 2
    tooltipText: root.tooltipText

    Row {
      id: iconRow
      anchors.centerIn: parent
      spacing: Style.space(6)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: RazerApi.deviceIcon("keyboard")
        color: root.iconColorFor(root.keyboardDevice)
        font.family: button.fontFamily
        font.pixelSize: button.fontSize
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: RazerApi.deviceIcon("mouse")
        color: root.iconColorFor(root.mouseDevice)
        font.family: button.fontFamily
        font.pixelSize: button.fontSize
      }
    }

    onPressed: function(b) {
      if (b === Qt.LeftButton) root.togglePanel()
    }
  }
}
