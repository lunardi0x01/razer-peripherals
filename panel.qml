import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui
import "razer_api.js" as RazerApi

Panel {
  id: root
  moduleName: "lunardi0x01.razer-peripherals"
  ipcTarget: "lunardi0x01.razer-peripherals"

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var devices: []
  property bool loading: false
  property bool lastFetchFailed: false
  // id of the device whose colour-apply Process is currently in flight --
  // VARSTORE writes go to flash, so only one at a time per device and the
  // Apply button for it is disabled while this is set, rather than queuing/
  // coalescing repeated writes the way Hue's slider does for live state.
  //
  // Keyed by device id (the physical peripheral), not pid (the interface
  // it currently answers on): a device's pid changes when its cable goes
  // in or comes out, and a half-typed colour shouldn't vanish because of
  // that.
  property string applyingId: ""
  property var pendingHexById: ({})

  readonly property var lowestPercent: {
    var lowest = null
    for (var i = 0; i < root.devices.length; i++) {
      var p = root.devices[i].percent
      if (p === null || p === undefined) continue
      if (lowest === null || p < lowest) lowest = p
    }
    return lowest
  }

  readonly property string statusText: {
    if (root.lastFetchFailed) return "Couldn't read devices"
    if (root.loading && root.devices.length === 0) return "Reading…"
    return ""
  }

  function hexFor(id) {
    if (Object.prototype.hasOwnProperty.call(root.pendingHexById, id)) {
      return root.pendingHexById[id]
    }
    for (var i = 0; i < root.devices.length; i++) {
      if (root.devices[i].id === id) return root.devices[i].lastColor || ""
    }
    return ""
  }

  function setHexFor(id, hex) {
    var next = {}
    for (var k in root.pendingHexById) next[k] = root.pendingHexById[k]
    next[id] = hex
    root.pendingHexById = next
  }

  function refresh() {
    if (statusProc.running) return
    root.loading = true
    statusProc.command = RazerApi.apiCmd(["get-status"])
    statusProc.running = true
  }

  function applyColor(id, pid, hex) {
    if (!RazerApi.isValidPid(pid) || !RazerApi.isValidHexColor(hex)) return
    if (root.applyingId !== "") return
    root.applyingId = id
    applyProc.forId = id
    applyProc.command = RazerApi.apiCmd(["set-color", pid, hex])
    applyProc.running = true
  }

  onOpenedChanged: if (opened) root.refresh()

  // Unlike Hue (whose bar icon carries no live data), this plugin's bar
  // icon itself shows the lowest battery %, so waiting for the panel to be
  // opened before ever fetching would leave the bar icon blank all
  // session -- fetch once at startup regardless of panel state.
  Component.onCompleted: root.refresh()

  Timer {
    // Fast poll while the panel is open, for a responsive-feeling UI.
    interval: 20000
    repeat: true
    running: root.opened
    onTriggered: root.refresh()
  }

  Timer {
    // Slow background poll so the bar icon's percentage doesn't go stale
    // for an entire shell session just because the panel was never
    // reopened -- battery drains over hours, so 5 minutes is plenty.
    interval: 300000
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  Process {
    id: statusProc
    stdout: StdioCollector {
      id: statusCollector
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.loading = false
      if (exitCode !== 0) {
        root.lastFetchFailed = true
        return
      }
      root.lastFetchFailed = false
      root.devices = RazerApi.parseStatus(statusCollector.text)
    }
  }

  Process {
    id: applyProc
    property string forId: ""
    onExited: function(exitCode) {
      if (applyProc.forId === root.applyingId) root.applyingId = ""
      if (exitCode === 0) {
        var next = {}
        for (var k in root.pendingHexById) {
          if (k !== applyProc.forId) next[k] = root.pendingHexById[k]
        }
        root.pendingHexById = next
        root.refresh()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          width: scroll.width
          spacing: Style.space(10)

          Column {
            width: parent.width
            spacing: Style.space(2)

            Text {
              text: "Razer Peripherals"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }

            Text {
              visible: root.statusText.length > 0
              text: root.statusText
              textFormat: Text.PlainText
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          PanelSeparator {
            foreground: root.bar.foreground
          }

          Text {
            visible: root.devices.length === 0 && !root.loading
            width: parent.width
            text: "No responsive Razer devices found. If one is wireless, wake it first (press a key / move it)."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.body
          }

          Repeater {
            model: root.devices

            delegate: Column {
              required property var modelData
              width: column.width
              spacing: Style.space(6)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: RazerApi.deviceIcon(modelData.kind)
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.name
                  textFormat: Text.PlainText
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: RazerApi.connectionLabel(modelData.connection).length > 0
                  text: RazerApi.connectionLabel(modelData.connection)
                  textFormat: Text.PlainText
                  color: Qt.darker(root.bar.foreground, 1.4)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: !modelData.responsive
                  text: "(asleep — last known)"
                  textFormat: Text.PlainText
                  color: Qt.darker(root.bar.foreground, 1.6)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Row {
                width: parent.width
                spacing: Style.space(8)

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: RazerApi.formatPercent(modelData.percent)
                  textFormat: Text.PlainText
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.title
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: modelData.charging
                  text: "charging"
                  textFormat: Text.PlainText
                  color: Qt.darker(root.bar.foreground, 1.4)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Row {
                width: parent.width
                spacing: Style.space(8)

                Rectangle {
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(22)
                  height: Style.space(22)
                  radius: Style.space(4)
                  border.width: 1
                  border.color: Qt.darker(root.bar.foreground, 1.6)
                  color: {
                    var hex = root.hexFor(modelData.id)
                    return RazerApi.isValidHexColor(hex) ? ("#" + hex) : "transparent"
                  }
                }

                TextField {
                  id: hexField
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(110)
                  foreground: root.bar.foreground
                  placeholderText: "RRGGBB"
                  text: root.hexFor(modelData.id)
                  onTextEdited: root.setHexFor(modelData.id, text)
                }

                Button {
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.applyingId === modelData.id ? "Saving…" : "Save to device"
                  bordered: true
                  foreground: root.bar.foreground
                  enabled: root.applyingId === "" && RazerApi.isValidHexColor(hexField.text)
                  opacity: enabled ? 1 : 0.5
                  tooltipText: "Writes to the device's flash memory — not a live preview"
                  onClicked: root.applyColor(modelData.id, modelData.pid, hexField.text)
                }
              }
            }
          }

          Text {
            width: parent.width
            text: "Saving writes the colour onto the device's flash memory."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: Qt.darker(root.bar.foreground, 1.6)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
